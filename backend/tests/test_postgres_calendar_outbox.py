"""PostgreSQL integration tests for Calendar outbox atomicity and leasing."""

from datetime import UTC, datetime
import asyncio
import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest

from app.calendar.outbox import CalendarActionOutbox
from app.db.postgres.protocol_repository import PostgresProtocolRepository
from app.models_calendar import CalendarCreateRequest, CalendarEventProposal
from app.models_protocols import ProtocolStatus
from app.protocols.service import ProtocolService
from app.safety.actions import HarnessAction
from app.safety.direct_actions import direct_action_request_hash


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="SOCIALEASE_TEST_DATABASE_URL is required.",
    ),
    pytest.mark.anyio,
]


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply the outbox migration before integration tests."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    command.upgrade(config, "head")


async def _approved_job(outbox: CalendarActionOutbox):
    assert TEST_DATABASE_URL is not None
    request = CalendarCreateRequest(
        user_id=f"pg_calendar_{uuid4().hex}",
        proposal=CalendarEventProposal(
            title="练习提醒",
            start_time=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            duration_minutes=15,
        ),
        idempotency_key=f"pg-calendar-{uuid4().hex}",
    )
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
        reason="integration",
        required_protocol="integration",
        session_id=None,
        request_hash=request_hash,
    )
    await protocols.respond(
        protocol_id=protocol.protocol_id,
        user_id=request.user_id,
        approved=True,
    )
    job = await outbox.enqueue(
        protocol_id=protocol.protocol_id,
        user_id=request.user_id,
        action_type="create",
        request_hash=request_hash,
        idempotency_key=request.idempotency_key,
        payload=request.model_dump(mode="json"),
    )
    assert job is not None
    stored_protocol = await protocols.store.get_for_user(
        protocol.protocol_id, request.user_id
    )
    assert stored_protocol is not None
    assert stored_protocol.status is ProtocolStatus.CONSUMED
    return job


async def test_postgres_enqueue_consumes_protocol_atomically() -> None:
    assert TEST_DATABASE_URL is not None
    outbox = CalendarActionOutbox(database_url=TEST_DATABASE_URL)

    job = await _approved_job(outbox)
    replay = await outbox.get(job.job_id)

    assert replay is not None
    assert replay.status == "pending"


async def test_postgres_skip_locked_allows_only_one_claim() -> None:
    assert TEST_DATABASE_URL is not None
    outbox = CalendarActionOutbox(database_url=TEST_DATABASE_URL)
    job = await _approved_job(outbox)

    claims = await asyncio.gather(
        outbox.claim(worker_id="worker-a", job_id=job.job_id),
        outbox.claim(worker_id="worker-b", job_id=job.job_id),
    )

    assert sum(len(batch) for batch in claims) == 1
