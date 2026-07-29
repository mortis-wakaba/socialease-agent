"""Execute leased Calendar outbox jobs with durable retry semantics."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.calendar.outbox import CalendarActionJob, CalendarActionOutbox
from app.calendar.service import CalendarService, calendar_service
from app.models_calendar import (
    CalendarEventProposal,
    CalendarEventResponse,
)
from app.observability.runtime_events import record_runtime_event


class CalendarOutboxUnavailable(RuntimeError):
    """Raised when an action is queued but cannot be completed now."""


class CalendarOutboxProcessor:
    """Claim, execute, verify, and durably finish Calendar actions."""

    def __init__(
        self,
        *,
        outbox: CalendarActionOutbox,
        service: CalendarService | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.outbox = outbox
        self.service = service or calendar_service
        self.worker_id = worker_id or f"calendar-worker:{uuid4().hex}"

    async def process_job(self, job_id: str) -> CalendarEventResponse:
        """Process one due job or return its already completed result."""
        existing = await self.outbox.get(job_id)
        if existing is None:
            raise LookupError("calendar outbox job not found")
        if existing.status == "completed" and existing.result is not None:
            return CalendarEventResponse.model_validate(existing.result)
        jobs = await self.outbox.claim(
            worker_id=self.worker_id,
            limit=1,
            job_id=job_id,
        )
        if not jobs:
            for _ in range(100):
                await asyncio.sleep(0.01)
                replay = await self.outbox.get(job_id)
                if replay is None:
                    raise LookupError("calendar outbox job not found")
                if replay.status == "completed" and replay.result is not None:
                    return CalendarEventResponse.model_validate(replay.result)
                if replay.status != "processing":
                    break
            raise CalendarOutboxUnavailable("calendar action is queued")
        job = jobs[0]
        try:
            response = await self._execute(job)
            completed = await self.outbox.complete(
                job_id=job.job_id,
                lease_owner=self.worker_id,
                result=response.model_dump(mode="json"),
            )
            if not completed:
                raise CalendarOutboxUnavailable(
                    "calendar action completion lease was lost"
                )
        except Exception as exc:
            status = await self.outbox.retry(
                job_id=job.job_id,
                lease_owner=self.worker_id,
                error_code=exc.__class__.__name__,
            )
            await record_runtime_event(
                "calendar_outbox_dead_letter"
                if status == "dead_letter"
                else "calendar_outbox_retry"
            )
            raise
        await record_runtime_event("calendar_outbox_completed")
        return response

    async def process_due(self, *, limit: int = 20) -> int:
        """Process a leased batch, isolating failures between jobs."""
        jobs = await self.outbox.claim(
            worker_id=self.worker_id,
            limit=limit,
        )
        for job in jobs:
            try:
                response = await self._execute(job)
                completed = await self.outbox.complete(
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                    result=response.model_dump(mode="json"),
                )
                if not completed:
                    raise CalendarOutboxUnavailable("calendar completion lease lost")
            except Exception as exc:
                status = await self.outbox.retry(
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                    error_code=exc.__class__.__name__,
                )
                await record_runtime_event(
                    "calendar_outbox_dead_letter"
                    if status == "dead_letter"
                    else "calendar_outbox_retry"
                )
            else:
                await record_runtime_event("calendar_outbox_completed")
        return len(jobs)

    async def _execute(self, job: CalendarActionJob) -> CalendarEventResponse:
        payload = job.payload
        if job.action_type == "create":
            return await self.service.create_event(
                user_id=job.user_id,
                proposal=CalendarEventProposal.model_validate(payload["proposal"]),
                idempotency_key=job.idempotency_key,
            )
        action_id = payload.get("calendar_action_id")
        if not isinstance(action_id, str):
            raise ValueError("calendar action id is missing")
        if job.action_type == "update":
            return await self.service.update_event(
                user_id=job.user_id,
                calendar_action_id=action_id,
                proposal=CalendarEventProposal.model_validate(payload["proposal"]),
            )
        return await self.service.delete_event(
            user_id=job.user_id,
            calendar_action_id=action_id,
        )
