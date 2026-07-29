"""Fault-injection tests for the transactional Calendar outbox."""

import asyncio
from datetime import UTC, datetime
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.calendar.mcp_client import CalendarMCPError, InProcessCalendarMCPClient
from app.calendar.outbox import CalendarActionOutbox
from app.calendar.outbox_processor import CalendarOutboxProcessor
from app.calendar.provider import InMemoryCalendarProvider
from app.calendar.service import CalendarService
from app.calendar.tools import CalendarToolService
from app.db.postgres.protocol_repository import PostgresProtocolRepository
from app.models_calendar import (
    CalendarCreateRequest,
    CalendarEventProposal,
    CalendarEventResponse,
)
from app.models_protocols import ProtocolStatus
from app.protocols.service import ProtocolService
from app.safety.actions import HarnessAction
from app.safety.direct_actions import direct_action_request_hash


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required.",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def outbox(
) -> CalendarActionOutbox:
    return CalendarActionOutbox(database_url=TEST_DATABASE_URL)


def _request() -> CalendarCreateRequest:
    user_id = f"calendar_owner_{uuid4().hex}"
    return CalendarCreateRequest(
        user_id=user_id,
        proposal=CalendarEventProposal(
            title="练习提醒",
            start_time=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            duration_minutes=15,
        ),
        idempotency_key=f"calendar-outbox-{uuid4().hex}",
    )


async def _enqueue_approved(outbox: CalendarActionOutbox):
    request = _request()
    request_hash = direct_action_request_hash(
        harness_action=HarnessAction.CREATE_CALENDAR_EVENT,
        payload=request,
    )
    protocols = ProtocolService(
        store=PostgresProtocolRepository(database_url=TEST_DATABASE_URL)
    )
    protocol = await protocols.create_consent_request(
        user_id=request.user_id,
        harness_action=HarnessAction.CREATE_CALENDAR_EVENT,
        reason="test",
        required_protocol="test",
        session_id=None,
        request_hash=request_hash,
    )
    approved = await protocols.respond(
        protocol_id=protocol.protocol_id,
        user_id=request.user_id,
        approved=True,
    )
    assert approved is not None
    job = await outbox.enqueue(
        protocol_id=protocol.protocol_id,
        user_id=request.user_id,
        action_type="create",
        request_hash=request_hash,
        idempotency_key=request.idempotency_key,
        payload=request.model_dump(mode="json"),
    )
    assert job is not None
    consumed = await protocols.store.get_for_user(
        protocol.protocol_id, request.user_id
    )
    assert consumed is not None
    assert consumed.status is ProtocolStatus.CONSUMED
    return job


@pytest.mark.anyio
async def test_consent_consumption_and_enqueue_are_atomic(
    outbox: CalendarActionOutbox,
) -> None:
    job = await _enqueue_approved(outbox)

    stored = await outbox.get(job.job_id)

    assert stored is not None
    assert stored.status == "pending"
    assert stored.attempt_count == 0


@pytest.mark.anyio
async def test_concurrent_claim_has_single_lease_owner(
    outbox: CalendarActionOutbox,
) -> None:
    job = await _enqueue_approved(outbox)

    first = await outbox.claim(worker_id="worker-a", job_id=job.job_id)
    second = await outbox.claim(worker_id="worker-b", job_id=job.job_id)

    assert len(first) == 1
    assert second == []
    assert first[0].lease_owner == "worker-a"


@pytest.mark.anyio
async def test_expired_lease_is_reclaimed(
    outbox: CalendarActionOutbox,
) -> None:
    job = await _enqueue_approved(outbox)
    assert await outbox.claim(worker_id="crashed-worker", job_id=job.job_id)
    async with outbox.engine.begin() as connection:
        await connection.execute(
            text(
                """UPDATE calendar_action_outbox
                SET lease_expires_at = :lease_expires_at
                WHERE job_id = :job_id"""
            ),
            {
                "lease_expires_at": datetime(2020, 1, 1, tzinfo=UTC),
                "job_id": job.job_id,
            },
        )

    reclaimed = await outbox.claim(
        worker_id="replacement-worker",
        job_id=job.job_id,
    )

    assert len(reclaimed) == 1
    assert reclaimed[0].lease_owner == "replacement-worker"
    assert reclaimed[0].attempt_count == 2


@pytest.mark.anyio
async def test_external_success_then_completion_failure_reconciles_idempotently(
    outbox: CalendarActionOutbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue_approved(outbox)
    provider = InMemoryCalendarProvider()
    service = CalendarService(
        InProcessCalendarMCPClient(CalendarToolService(provider))
    )
    original_complete = outbox.complete
    failed_once = False

    async def fail_once(**kwargs: object) -> bool:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            return False
        return await original_complete(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(outbox, "complete", fail_once)
    processor = CalendarOutboxProcessor(
        outbox=outbox,
        service=service,
        worker_id="worker",
    )

    with pytest.raises(Exception, match="completion lease"):
        await processor.process_job(job.job_id)
    async with outbox.engine.begin() as connection:
        await connection.execute(
            text(
                """UPDATE calendar_action_outbox
                SET next_attempt_at = :next_attempt_at
                WHERE job_id = :job_id"""
            ),
            {
                "next_attempt_at": datetime(2020, 1, 1, tzinfo=UTC),
                "job_id": job.job_id,
            },
        )
    response = await processor.process_job(job.job_id)
    events = await service.list_owned_events(user_id=job.user_id)

    assert response.verified is True
    assert response.event.idempotency_reused is True
    assert len(events) == 1
    assert (await outbox.get(job.job_id)).status == "completed"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_concurrent_processing_waits_for_leased_job_result(
    outbox: CalendarActionOutbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue_approved(outbox)
    provider = InMemoryCalendarProvider()
    service = CalendarService(
        InProcessCalendarMCPClient(CalendarToolService(provider))
    )
    original_create = service.create_event
    call_count = 0

    async def slow_create(**kwargs: object) -> CalendarEventResponse:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(1.2)
        return await original_create(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "create_event", slow_create)
    processors = [
        CalendarOutboxProcessor(
            outbox=outbox,
            service=service,
            worker_id=f"concurrent-worker-{index}",
        )
        for index in range(8)
    ]

    responses = await asyncio.gather(
        *(processor.process_job(job.job_id) for processor in processors)
    )

    assert all(response.verified for response in responses)
    assert len(
        {response.event.calendar_action_id for response in responses}
    ) == 1
    assert call_count == 1
    assert len(await service.list_owned_events(user_id=job.user_id)) == 1


@pytest.mark.anyio
async def test_max_attempt_moves_job_to_dead_letter(
    outbox: CalendarActionOutbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue_approved(outbox)
    service = CalendarService(
        InProcessCalendarMCPClient(
            CalendarToolService(InMemoryCalendarProvider())
        )
    )

    async def fail(**_kwargs: object):
        raise CalendarMCPError("unavailable")

    monkeypatch.setattr(service, "create_event", fail)
    async with outbox.engine.begin() as connection:
        await connection.execute(
            text(
                """UPDATE calendar_action_outbox
                SET max_attempts = 1 WHERE job_id = :job_id"""
            ),
            {"job_id": job.job_id},
        )
    processor = CalendarOutboxProcessor(
        outbox=outbox,
        service=service,
        worker_id="worker",
    )

    with pytest.raises(CalendarMCPError):
        await processor.process_job(job.job_id)

    stored = await outbox.get(job.job_id)
    assert stored is not None
    assert stored.status == "dead_letter"
    health = await outbox.health()
    assert health.dead_letter >= 1
