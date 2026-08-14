"""Reconcile completed outbox jobs into the durable intent-effect ledger."""

from datetime import UTC, datetime

from app.calendar.outbox import CalendarActionOutbox
from app.conversation.intent_queue import (
    complete_current_intent_step,
    undo_calendar_step_effect,
)
from app.conversation.repository import (
    ConversationConcurrencyError,
    ConversationRepository,
)
from app.models import Intent, IntentStepEffect
from app.models_calendar import CalendarEventResponse
from app.models_intent_effect import (
    IntentEffectLedgerRecord,
    IntentEffectLedgerStatus,
)


class IntentEffectReconciler:
    """Project asynchronously completed Calendar jobs back into plan state."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        outbox: CalendarActionOutbox,
    ) -> None:
        self.repository = repository
        self.outbox = outbox

    async def run_once(self, *, limit: int = 100) -> int:
        """Reconcile a bounded batch with optimistic multi-worker safety."""
        records = await self.repository.list_pending_intent_effects(limit=limit)
        reconciled = 0
        for record in records:
            job_id = (
                record.create_job_id
                if record.status == IntentEffectLedgerStatus.APPLY_PENDING
                else record.undo_job_id
            )
            if job_id is None:
                continue
            job = await self.outbox.get(job_id)
            if job is None or job.status != "completed" or job.result is None:
                continue
            now = datetime.now(UTC)
            try:
                if record.status == IntentEffectLedgerStatus.APPLY_PENDING:
                    response = CalendarEventResponse.model_validate(job.result)
                    transitioned = record.model_copy(
                        update={
                            "status": IntentEffectLedgerStatus.APPLIED,
                            "external_action_id": (
                                response.event.calendar_action_id
                            ),
                            "updated_at": now,
                        }
                    )
                    transitioned = await self.repository.update_intent_effect(
                        transitioned,
                        expected_version=record.version,
                    )
                    await self._complete_matching_plan(transitioned, now=now)
                else:
                    transitioned = record.model_copy(
                        update={
                            "status": IntentEffectLedgerStatus.UNDONE,
                            "updated_at": now,
                        }
                    )
                    transitioned = await self.repository.update_intent_effect(
                        transitioned,
                        expected_version=record.version,
                    )
                    await self._undo_matching_plan(transitioned, now=now)
            except ConversationConcurrencyError:
                continue
            reconciled += 1
        return reconciled

    async def _complete_matching_plan(
        self,
        record: IntentEffectLedgerRecord,
        *,
        now: datetime,
    ) -> None:
        if record.external_action_id is None:
            return
        plan = await self.repository.get_intent_plan(
            conversation_id=record.conversation_id,
            user_id=record.user_id,
        )
        if plan is None or plan.plan_id != record.plan_id:
            return
        completed = complete_current_intent_step(
            plan,
            expected_intent=Intent.CALENDAR_PLANNING,
            effect=IntentStepEffect(
                external_action_id=record.external_action_id,
                idempotency_key=record.create_idempotency_key,
                applied_at=now,
            ),
            now=now,
        )
        if completed is not plan:
            await self.repository.update_intent_plan(
                completed,
                expected_version=plan.version,
            )

    async def _undo_matching_plan(
        self,
        record: IntentEffectLedgerRecord,
        *,
        now: datetime,
    ) -> None:
        plan = await self.repository.get_intent_plan(
            conversation_id=record.conversation_id,
            user_id=record.user_id,
        )
        if plan is None or plan.plan_id != record.plan_id:
            return
        undone = undo_calendar_step_effect(
            plan,
            step_id=record.step_id,
            now=now,
        )
        if undone is not plan:
            await self.repository.update_intent_plan(
                undone,
                expected_version=plan.version,
            )
