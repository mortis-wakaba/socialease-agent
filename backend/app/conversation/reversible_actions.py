"""Authorized reversible side effects for sequential conversation plans."""

from dataclasses import dataclass

from app.calendar.outbox import CalendarActionOutbox
from app.calendar.outbox_processor import CalendarOutboxProcessor
from app.calendar.outbox_processor import CalendarOutboxUnavailable
from app.calendar.mcp_client import CalendarMCPError
from app.calendar.provider import CalendarEventNotFoundError
from app.calendar.service import CalendarVerificationError
from app.models_calendar import (
    CalendarCreateRequest,
    CalendarEventProposal,
    CalendarEventResponse,
)
from app.safety.actions import HarnessAction
from app.safety.direct_actions import direct_action_request_hash


class ReversibleActionPending(RuntimeError):
    """Raised after an idempotent compensation job is durably queued."""

    def __init__(self, job_id: str) -> None:
        super().__init__("reversible action is queued for retry")
        self.job_id = job_id


@dataclass(frozen=True)
class ReversibleActionExecution:
    """Verified result paired with its durable outbox identity."""

    job_id: str
    response: CalendarEventResponse


class ReversibleCalendarExecutor:
    """Execute and compensate plan-authorized calendar actions via the outbox."""

    def __init__(
        self,
        *,
        outbox: CalendarActionOutbox | None = None,
        processor: CalendarOutboxProcessor | None = None,
    ) -> None:
        self.outbox = outbox or CalendarActionOutbox()
        self.processor = processor or CalendarOutboxProcessor(outbox=self.outbox)

    async def create(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> ReversibleActionExecution:
        """Create an explicitly authorized event and verify the write."""
        request = CalendarCreateRequest(
            user_id=user_id,
            proposal=proposal,
            idempotency_key=idempotency_key,
        )
        request_hash = direct_action_request_hash(
            harness_action=HarnessAction.CREATE_CALENDAR_EVENT,
            payload=request,
        )
        job = await self.outbox.enqueue(
            protocol_id=None,
            user_id=user_id,
            action_type="create",
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            payload=request.model_dump(mode="json"),
        )
        if job is None:
            raise RuntimeError("calendar create could not be enqueued")
        try:
            response = await self.processor.process_job(job.job_id)
        except (
            CalendarMCPError,
            CalendarVerificationError,
            CalendarOutboxUnavailable,
        ) as exc:
            raise ReversibleActionPending(job.job_id) from exc
        return ReversibleActionExecution(job_id=job.job_id, response=response)

    async def undo(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        idempotency_key: str,
    ) -> ReversibleActionExecution:
        """Compensate one SocialEase-owned calendar create and verify deletion."""
        payload: dict[str, object] = {
            "calendar_action_id": calendar_action_id,
            "user_id": user_id,
        }
        request_hash = direct_action_request_hash(
            harness_action=HarnessAction.DELETE_CALENDAR_EVENT,
            payload=payload,
        )
        job = await self.outbox.enqueue(
            protocol_id=None,
            user_id=user_id,
            action_type="delete",
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if job is None:
            raise RuntimeError("calendar undo could not be enqueued")
        try:
            response = await self.processor.process_job(job.job_id)
        except (
            CalendarEventNotFoundError,
            CalendarMCPError,
            CalendarVerificationError,
            CalendarOutboxUnavailable,
        ) as exc:
            raise ReversibleActionPending(job.job_id) from exc
        return ReversibleActionExecution(job_id=job.job_id, response=response)
