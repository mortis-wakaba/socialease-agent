"""Unit tests for plan-authorized reversible calendar actions."""

from datetime import UTC, datetime

import pytest

from app.calendar.outbox import CalendarActionJob
from app.calendar.outbox_processor import CalendarOutboxUnavailable
from app.conversation.reversible_actions import (
    ReversibleActionPending,
    ReversibleCalendarExecutor,
)
from app.models_calendar import (
    CalendarEventProposal,
    CalendarEventRecord,
    CalendarEventResponse,
    CalendarEventStatus,
    CalendarToolTrace,
)


class FakeOutbox:
    """Record plan-authorized jobs without a database."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def enqueue(self, **values: object) -> CalendarActionJob:
        self.calls.append(values)
        return CalendarActionJob(
            job_id=f"job-{len(self.calls)}",
            protocol_id=None,
            user_id=str(values["user_id"]),
            action_type=values["action_type"],  # type: ignore[arg-type]
            request_hash=str(values["request_hash"]),
            idempotency_key=str(values["idempotency_key"]),
            payload=values["payload"],  # type: ignore[arg-type]
            status="pending",
            attempt_count=0,
            max_attempts=3,
        )


class FakeProcessor:
    """Return verified provider-shaped results for create and undo jobs."""

    def __init__(self, proposal: CalendarEventProposal) -> None:
        self.proposal = proposal
        self.calls: list[str] = []

    async def process_job(self, job_id: str) -> CalendarEventResponse:
        self.calls.append(job_id)
        cancelled = len(self.calls) > 1
        now = datetime.now(UTC)
        return CalendarEventResponse(
            event=CalendarEventRecord(
                calendar_action_id="calendar-action-1",
                provider_event_id="provider-event-1",
                user_id="owner",
                proposal=self.proposal,
                idempotency_key="calendar-create-1",
                status=(
                    CalendarEventStatus.CANCELLED
                    if cancelled
                    else CalendarEventStatus.CONFIRMED
                ),
                created_at=now,
                updated_at=now,
            ),
            verified=True,
            tool_trace=CalendarToolTrace(
                tool_name="delete" if cancelled else "create",
                transport="fake",
                status="success",
                latency_ms=0,
                read_after_write_verified=True,
            ),
        )


class UnavailableProcessor:
    """Simulate a durably queued provider action awaiting retry."""

    async def process_job(self, job_id: str) -> CalendarEventResponse:
        del job_id
        raise CalendarOutboxUnavailable("queued")


@pytest.mark.anyio
async def test_executor_enqueues_protocol_free_create_and_compensation() -> None:
    proposal = CalendarEventProposal(
        title="15分钟练习",
        start_time=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        duration_minutes=15,
    )
    outbox = FakeOutbox()
    processor = FakeProcessor(proposal)
    executor = ReversibleCalendarExecutor(
        outbox=outbox,  # type: ignore[arg-type]
        processor=processor,  # type: ignore[arg-type]
    )

    created = await executor.create(
        user_id="owner",
        proposal=proposal,
        idempotency_key="calendar-create-1",
    )
    undone = await executor.undo(
        user_id="owner",
        calendar_action_id=created.response.event.calendar_action_id,
        idempotency_key="calendar-undo-1",
    )

    assert [call["action_type"] for call in outbox.calls] == [
        "create",
        "delete",
    ]
    assert all(call["protocol_id"] is None for call in outbox.calls)
    assert undone.response.event.status == CalendarEventStatus.CANCELLED


@pytest.mark.anyio
async def test_executor_exposes_durable_pending_job_for_safe_retry() -> None:
    proposal = CalendarEventProposal(
        title="15分钟练习",
        start_time=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        duration_minutes=15,
    )
    outbox = FakeOutbox()
    executor = ReversibleCalendarExecutor(
        outbox=outbox,  # type: ignore[arg-type]
        processor=UnavailableProcessor(),  # type: ignore[arg-type]
    )

    with pytest.raises(ReversibleActionPending) as captured:
        await executor.create(
            user_id="owner",
            proposal=proposal,
            idempotency_key="calendar-pending-1",
        )

    assert captured.value.job_id == "job-1"
    assert len(outbox.calls) == 1
