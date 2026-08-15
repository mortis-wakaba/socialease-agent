"""Execute reversible side effects authorized by conversation intent plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.conversation.intent_queue import complete_current_intent_step
from app.conversation.repository import ConversationRepository
from app.conversation.reversible_actions import (
    ReversibleActionPending,
    ReversibleCalendarExecutor,
)
from app.models import Intent, IntentExecutionStep, IntentStepEffect
from app.models_calendar import CalendarEventProposal
from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
)
from app.models_intent_effect import (
    IntentEffectLedgerRecord,
    IntentEffectLedgerStatus,
)
from app.models_intent_plan import ConversationIntentPlan
from app.skills.calendar import build_calendar_event_proposal


@dataclass(frozen=True)
class IntentEffectExecutionResult:
    """Persisted outcome of one supported reversible intent step."""

    plan: ConversationIntentPlan
    event: ConversationEvent
    response: str
    completed: bool


class IntentEffectExecutor:
    """Own calendar side-effect execution, receipts, and plan completion."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        calendar_executor: ReversibleCalendarExecutor,
        proposal_builder: Callable[
            [str], CalendarEventProposal | None
        ] = build_calendar_event_proposal,
    ) -> None:
        self._repository = repository
        self._calendar_executor = calendar_executor
        self._proposal_builder = proposal_builder

    async def execute(
        self,
        *,
        plan: ConversationIntentPlan,
        step: IntentExecutionStep,
        source_event: ConversationEvent,
    ) -> IntentEffectExecutionResult | None:
        """Execute a supported step, returning ``None`` when it cannot run."""
        if step.intent != Intent.CALENDAR_PLANNING:
            return None
        proposal = self._proposal_builder(source_event.content)
        if proposal is None:
            return None

        idempotency_key = f"intent-calendar:{plan.plan_id}:{step.step_id}"
        try:
            execution = await self._calendar_executor.create(
                user_id=plan.user_id,
                proposal=proposal,
                idempotency_key=idempotency_key,
            )
        except ReversibleActionPending as pending:
            return await self._record_pending(
                plan=plan,
                step=step,
                job_id=pending.job_id,
                idempotency_key=idempotency_key,
            )

        now = datetime.now(UTC)
        action_id = execution.response.event.calendar_action_id
        stored_effect = await self._repository.save_intent_effect(
            self._ledger_record(
                plan=plan,
                step=step,
                status=IntentEffectLedgerStatus.APPLIED,
                job_id=execution.job_id,
                idempotency_key=idempotency_key,
                now=now,
                external_action_id=action_id,
            )
        )
        if stored_effect.status == IntentEffectLedgerStatus.APPLY_PENDING:
            stored_effect = await self._repository.update_intent_effect(
                stored_effect.model_copy(
                    update={
                        "status": IntentEffectLedgerStatus.APPLIED,
                        "external_action_id": action_id,
                        "updated_at": now,
                    }
                ),
                expected_version=stored_effect.version,
            )

        calendar_event = execution.response.event
        response = (
            "已按你的明确请求写入日历："
            f"{calendar_event.proposal.title}，"
            f"{calendar_event.proposal.start_time.isoformat()}。"
            "如果不需要了，直接说“取消提醒”即可撤销。"
        )
        event = await self._append_response_event(
            plan=plan,
            step=step,
            response=response,
            pending=False,
        )
        completed = complete_current_intent_step(
            plan,
            expected_intent=step.intent,
            effect=IntentStepEffect(
                external_action_id=action_id,
                idempotency_key=idempotency_key,
                applied_at=now,
            ),
        )
        if completed is plan:
            return IntentEffectExecutionResult(
                plan=plan,
                event=event,
                response=response,
                completed=False,
            )
        updated_plan = await self._repository.update_intent_plan(
            completed,
            expected_version=plan.version,
        )
        return IntentEffectExecutionResult(
            plan=updated_plan,
            event=event,
            response=response,
            completed=True,
        )

    async def _record_pending(
        self,
        *,
        plan: ConversationIntentPlan,
        step: IntentExecutionStep,
        job_id: str,
        idempotency_key: str,
    ) -> IntentEffectExecutionResult:
        now = datetime.now(UTC)
        await self._repository.save_intent_effect(
            self._ledger_record(
                plan=plan,
                step=step,
                status=IntentEffectLedgerStatus.APPLY_PENDING,
                job_id=job_id,
                idempotency_key=idempotency_key,
                now=now,
            )
        )
        response = (
            "日历写入已经进入重试队列，系统会复用同一个任务，"
            "不会重复创建事件。你也可以稍后重复原请求查询结果。"
        )
        event = await self._append_response_event(
            plan=plan,
            step=step,
            response=response,
            pending=True,
        )
        return IntentEffectExecutionResult(
            plan=plan,
            event=event,
            response=response,
            completed=False,
        )

    @staticmethod
    def _ledger_record(
        *,
        plan: ConversationIntentPlan,
        step: IntentExecutionStep,
        status: IntentEffectLedgerStatus,
        job_id: str,
        idempotency_key: str,
        now: datetime,
        external_action_id: str | None = None,
    ) -> IntentEffectLedgerRecord:
        return IntentEffectLedgerRecord(
            effect_id=uuid4().hex,
            plan_id=plan.plan_id,
            step_id=step.step_id,
            conversation_id=plan.conversation_id,
            user_id=plan.user_id,
            kind="calendar_event",
            status=status,
            create_job_id=job_id,
            external_action_id=external_action_id,
            create_idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )

    async def _append_response_event(
        self,
        *,
        plan: ConversationIntentPlan,
        step: IntentExecutionStep,
        response: str,
        pending: bool,
    ) -> ConversationEvent:
        suffix = "effect-pending" if pending else "effect"
        return await self._repository.append_event(
            conversation_id=plan.conversation_id,
            user_id=plan.user_id,
            event_type=ConversationEventType.ASSISTANT_MESSAGE,
            role=ConversationEventRole.ASSISTANT,
            content=response,
            idempotency_key=f"intent-plan-{suffix}:{plan.plan_id}:{step.step_id}",
        )
