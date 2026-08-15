"""Focused coordinators for non-crisis conversation turn branches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.conversation.context_manager import ConversationContextManager
from app.conversation.intent_queue import (
    IntentQueueControl,
    IntentQueueControlAction,
    apply_intent_queue_control,
    detect_intent_queue_control,
    undo_calendar_step_effect,
)
from app.conversation.module_catalog import (
    module_proposal_copy,
    module_type_for_intent,
)
from app.conversation.module_coordinator import ModuleCoordinator
from app.conversation.module_policy import ModuleStackPolicy
from app.conversation.repository import ConversationRepository
from app.conversation.reversible_actions import (
    ReversibleActionPending,
    ReversibleCalendarExecutor,
)
from app.conversation.turns import ConversationTurn
from app.models import (
    Intent,
    IntentExecutionStep,
    IntentResult,
    IntentStepStatus,
    RiskLevel,
    SafetyResult,
)
from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ModuleProposal,
    ModuleProposalEventPayload,
    ModuleType,
)
from app.models_conversation_context import (
    ConversationContextDiagnostics,
    ConversationWorkingContext,
)
from app.models_intent_effect import IntentEffectLedgerStatus
from app.models_intent_plan import ConversationIntentPlan
from app.safety.classifier import BaseSafetyClassifier


class ReadyIntentStepConsumer(Protocol):
    """Execute ready intent-plan steps through the owning application service."""

    async def __call__(
        self,
        *,
        plan: ConversationIntentPlan,
        source_event: ConversationEvent,
        safety_result: SafetyResult,
        context: ConversationWorkingContext,
    ) -> tuple[ConversationIntentPlan, list[ConversationEvent], list[str]]: ...


class ProposalFactory(Protocol):
    """Persist a bounded module proposal for a routed turn."""

    async def __call__(
        self,
        *,
        conversation_id: str,
        user_id: str,
        source_event: ConversationEvent,
        message: str,
        module_type: ModuleType,
    ) -> ModuleProposal: ...


class IntentRoute(Protocol):
    """Route the current message without owning router lifecycle."""

    async def __call__(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult: ...


@dataclass(frozen=True)
class TurnResolution:
    """A completed branch ready for the API response assembler."""

    appended_events: list[ConversationEvent]
    response: str
    context_diagnostics: ConversationContextDiagnostics
    pending_proposal: ModuleProposal | None = None


@dataclass(frozen=True)
class IntentDispatchResult:
    """Return the routed intent and any branch completed by the coordinator."""

    intent_result: IntentResult
    resolution: TurnResolution | None


class IntentQueueCoordinator:
    """Own cancellation, compensation, and queued-step resumption."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        safety_classifier: BaseSafetyClassifier,
        context_manager: ConversationContextManager,
        module_coordinator: ModuleCoordinator,
        reversible_calendar_executor: ReversibleCalendarExecutor,
        consume_ready_steps: ReadyIntentStepConsumer,
    ) -> None:
        self._repository = repository
        self._safety_classifier = safety_classifier
        self._context_manager = context_manager
        self._module_coordinator = module_coordinator
        self._reversible_calendar_executor = reversible_calendar_executor
        self._consume_ready_steps = consume_ready_steps

    async def handle(self, turn: ConversationTurn) -> TurnResolution | None:
        """Resolve queue control or an idempotent reversible-step retry."""
        queue_control = detect_intent_queue_control(turn.message)
        if self._should_compensate_calendar(turn, queue_control):
            resolution = await self._compensate_calendar(turn)
            if resolution is not None:
                return resolution

        if turn.intent_plan is not None and queue_control is not None:
            return await self._apply_queue_control(turn, queue_control)

        if turn.intent_plan is not None and queue_control is None:
            return await self._retry_reversible_step(turn)
        return None

    @staticmethod
    def _should_compensate_calendar(
        turn: ConversationTurn,
        queue_control: IntentQueueControl | None,
    ) -> bool:
        if (
            queue_control is None
            or queue_control.action != IntentQueueControlAction.CANCEL_INTENT
            or queue_control.target_intent != Intent.CALENDAR_PLANNING
        ):
            return False
        return not any(
            step.intent == Intent.CALENDAR_PLANNING
            and step.status
            in {
                IntentStepStatus.READY,
                IntentStepStatus.READY_WITH_UNDO,
                IntentStepStatus.AWAITING_CONSENT,
                IntentStepStatus.ACTIVE,
            }
            for step in (
                turn.intent_plan.execution_plan.steps
                if turn.intent_plan is not None
                else []
            )
        )

    async def _compensate_calendar(
        self,
        turn: ConversationTurn,
    ) -> TurnResolution | None:
        effect = await self._repository.latest_applied_intent_effect(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
        )
        if effect is None or effect.external_action_id is None:
            return None

        undo_key = f"intent-calendar-undo:{effect.effect_id}"
        try:
            execution = await self._reversible_calendar_executor.undo(
                user_id=turn.user_id,
                calendar_action_id=effect.external_action_id,
                idempotency_key=undo_key,
            )
        except ReversibleActionPending as pending:
            now = datetime.now(UTC)
            await self._repository.update_intent_effect(
                effect.model_copy(
                    update={
                        "status": IntentEffectLedgerStatus.UNDO_PENDING,
                        "undo_job_id": pending.job_id,
                        "undo_idempotency_key": undo_key,
                        "updated_at": now,
                    }
                ),
                expected_version=effect.version,
            )
            return await self._acknowledge(
                turn,
                response=(
                    "撤销操作已经进入重试队列；重复说“取消提醒”"
                    "会复用同一个撤销任务。"
                ),
                idempotency_key=(
                    f"intent-plan-undo-pending:{turn.idempotency_key}"
                ),
            )

        now = datetime.now(UTC)
        effect = await self._repository.update_intent_effect(
            effect.model_copy(
                update={
                    "status": IntentEffectLedgerStatus.UNDONE,
                    "undo_job_id": execution.job_id,
                    "undo_idempotency_key": undo_key,
                    "updated_at": now,
                }
            ),
            expected_version=effect.version,
        )
        if (
            turn.intent_plan is not None
            and turn.intent_plan.plan_id == effect.plan_id
        ):
            undone = undo_calendar_step_effect(
                turn.intent_plan,
                step_id=effect.step_id,
            )
            if undone is not turn.intent_plan:
                turn.intent_plan = await self._repository.update_intent_plan(
                    undone,
                    expected_version=turn.intent_plan.version,
                )
        return await self._acknowledge(
            turn,
            response="好的，之前创建的日历提醒已经撤销。",
            idempotency_key=f"intent-plan-undo:{turn.idempotency_key}",
        )

    async def _acknowledge(
        self,
        turn: ConversationTurn,
        *,
        response: str,
        idempotency_key: str,
    ) -> TurnResolution:
        context = await self._assemble_context(turn)
        event = await self._repository.append_event(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            event_type=ConversationEventType.ASSISTANT_MESSAGE,
            role=ConversationEventRole.ASSISTANT,
            content=response,
            idempotency_key=idempotency_key,
        )
        return TurnResolution(
            appended_events=[turn.user_event, event],
            response=response,
            context_diagnostics=context.diagnostics,
        )

    async def _apply_queue_control(
        self,
        turn: ConversationTurn,
        queue_control: IntentQueueControl,
    ) -> TurnResolution:
        plan = turn.intent_plan
        assert plan is not None
        updated, acknowledgement = apply_intent_queue_control(plan, queue_control)
        if updated is not plan:
            turn.intent_plan = await self._repository.update_intent_plan(
                updated,
                expected_version=plan.version,
            )

        lifecycle_events: list[ConversationEvent] = []
        should_end_active = queue_control.action in {
            IntentQueueControlAction.CANCEL_CURRENT,
            IntentQueueControlAction.CANCEL_ALL,
        }
        if (
            queue_control.action == IntentQueueControlAction.CANCEL_INTENT
            and queue_control.target_intent is not None
            and turn.active_run is not None
        ):
            should_end_active = (
                module_type_for_intent(queue_control.target_intent)
                == turn.active_run.module_type
            )
        if turn.stack and should_end_active:
            assert turn.active_run is not None
            control_response = (
                await self._module_coordinator.terminate_all(
                    conversation_id=turn.conversation_id,
                    user_id=turn.user_id,
                )
                if queue_control.action == IntentQueueControlAction.CANCEL_ALL
                else await self._module_coordinator.terminate_current(
                    conversation_id=turn.conversation_id,
                    user_id=turn.user_id,
                    module_run_id=turn.active_run.module_run_id,
                )
            )
            lifecycle_events.extend(control_response.appended_events)

        context = await self._assemble_context(turn)
        progression_events: list[ConversationEvent] = []
        progression_responses: list[str] = []
        plan = turn.intent_plan
        assert plan is not None
        source_event = await self._repository.get_event_for_user(
            event_id=plan.source_event_id,
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
        )
        if source_event is not None:
            original_safety = await self._safety_classifier.classify(
                source_event.content
            )
            (
                turn.intent_plan,
                progression_events,
                progression_responses,
            ) = await self._consume_ready_steps(
                plan=plan,
                source_event=source_event,
                safety_result=original_safety,
                context=context,
            )

        response = acknowledgement
        if progression_responses:
            response += "\n\n接下来：" + "\n\n".join(progression_responses)
        event = await self._repository.append_event(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            event_type=ConversationEventType.ASSISTANT_MESSAGE,
            role=ConversationEventRole.ASSISTANT,
            content=acknowledgement,
            idempotency_key=f"intent-plan-control:{turn.idempotency_key}",
        )
        return TurnResolution(
            appended_events=[
                turn.user_event,
                *lifecycle_events,
                *progression_events,
                event,
            ],
            response=response,
            context_diagnostics=context.diagnostics,
        )

    async def _retry_reversible_step(
        self,
        turn: ConversationTurn,
    ) -> TurnResolution | None:
        plan = turn.intent_plan
        assert plan is not None
        step = _current_intent_step(plan)
        if (
            step is None
            or step.status != IntentStepStatus.READY_WITH_UNDO
            or step.intent != Intent.CALENDAR_PLANNING
        ):
            return None
        source_event = await self._repository.get_event_for_user(
            event_id=plan.source_event_id,
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
        )
        if (
            source_event is None
            or source_event.content.strip() != turn.message.strip()
        ):
            return None
        context = await self._assemble_context(turn)
        turn.intent_plan, events, responses = await self._consume_ready_steps(
            plan=plan,
            source_event=source_event,
            safety_result=turn.safety_result,
            context=context,
        )
        if not responses:
            return None
        return TurnResolution(
            appended_events=[turn.user_event, *events],
            response="\n\n".join(responses),
            context_diagnostics=context.diagnostics,
        )

    async def _assemble_context(
        self,
        turn: ConversationTurn,
    ) -> ConversationWorkingContext:
        return await self._context_manager.assemble(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            current_user_message=turn.message,
            current_event_id=turn.user_event.event_id,
        )


class IntentDispatchCoordinator:
    """Own intent-plan creation and module proposal/start decisions."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        route_intent: IntentRoute,
        context_manager: ConversationContextManager,
        module_coordinator: ModuleCoordinator,
        consume_ready_steps: ReadyIntentStepConsumer,
        create_proposal: ProposalFactory,
    ) -> None:
        self._repository = repository
        self._route_intent = route_intent
        self._context_manager = context_manager
        self._module_coordinator = module_coordinator
        self._consume_ready_steps = consume_ready_steps
        self._create_proposal = create_proposal

    async def handle(self, turn: ConversationTurn) -> IntentDispatchResult:
        """Route a turn and resolve automatic effects or module proposals."""
        intent_result = await self._route_intent(
            turn.message,
            turn.safety_result,
        )
        await self._persist_execution_plan(turn, intent_result)
        primary_step = _primary_step(intent_result)

        resolution = await self._consume_primary_effect(
            turn,
            primary_step=primary_step,
        )
        if resolution is None:
            resolution = await self._route_module(
                turn,
                intent_result=intent_result,
                primary_step=primary_step,
            )
        return IntentDispatchResult(
            intent_result=intent_result,
            resolution=resolution,
        )

    async def _persist_execution_plan(
        self,
        turn: ConversationTurn,
        intent_result: IntentResult,
    ) -> None:
        execution_plan = intent_result.execution_plan
        if (
            turn.stack
            or execution_plan is None
            or not execution_plan.steps
            or (
                len(execution_plan.steps) == 1
                and execution_plan.steps[0].status
                != IntentStepStatus.READY_WITH_UNDO
            )
        ):
            return
        now = datetime.now(UTC)
        turn.intent_plan = await self._repository.save_intent_plan(
            ConversationIntentPlan(
                plan_id=uuid4().hex,
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                source_event_id=turn.user_event.event_id,
                execution_plan=execution_plan,
                created_at=now,
                updated_at=now,
            )
        )

    async def _consume_primary_effect(
        self,
        turn: ConversationTurn,
        *,
        primary_step: IntentExecutionStep | None,
    ) -> TurnResolution | None:
        if (
            turn.intent_plan is None
            or primary_step is None
            or primary_step.status != IntentStepStatus.READY_WITH_UNDO
        ):
            return None
        context = await self._assemble_context(turn)
        turn.intent_plan, events, responses = await self._consume_ready_steps(
            plan=turn.intent_plan,
            source_event=turn.user_event,
            safety_result=turn.safety_result,
            context=context,
        )
        if not responses:
            return None
        return TurnResolution(
            appended_events=[turn.user_event, *events],
            response="\n\n".join(responses),
            context_diagnostics=context.diagnostics,
        )

    async def _route_module(
        self,
        turn: ConversationTurn,
        *,
        intent_result: IntentResult,
        primary_step: IntentExecutionStep | None,
    ) -> TurnResolution | None:
        proposed_module = module_type_for_intent(intent_result.intent)
        if (
            proposed_module is None
            or turn.safety_result.risk_level != RiskLevel.LOW
            or not ModuleStackPolicy.can_push(turn.stack, proposed_module)
        ):
            return None
        context = await self._assemble_context(turn)
        proposal = await self._create_proposal(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            source_event=turn.user_event,
            message=turn.message,
            module_type=proposed_module,
        )
        if (
            primary_step is not None
            and primary_step.status == IntentStepStatus.READY
        ):
            started = await self._module_coordinator.accept(proposal, context)
            if turn.intent_plan is not None:
                prior_version = turn.intent_plan.version
                turn.intent_plan.execution_plan.steps[0].status = (
                    IntentStepStatus.ACTIVE
                )
                turn.intent_plan.updated_at = datetime.now(UTC)
                turn.intent_plan = await self._repository.update_intent_plan(
                    turn.intent_plan,
                    expected_version=prior_version,
                )
            return TurnResolution(
                appended_events=[turn.user_event, *started.appended_events],
                response=started.response,
                context_diagnostics=context.diagnostics,
            )

        copy = module_proposal_copy(proposed_module)
        event = await self._repository.get_event_by_idempotency(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            idempotency_key=f"proposal:{turn.idempotency_key}",
        )
        if event is None:
            event = await self._repository.append_event(
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                event_type=ConversationEventType.MODULE_PROPOSED,
                role=ConversationEventRole.ASSISTANT,
                content=copy,
                structured_payload=ModuleProposalEventPayload(
                    proposal_id=proposal.proposal_id,
                    proposed_module=proposal.proposed_module,
                    reason_code=proposal.reason_code,
                ),
                module_run_id=(
                    turn.active_run.module_run_id if turn.active_run else None
                ),
                parent_module_run_id=(
                    turn.active_run.parent_module_run_id
                    if turn.active_run
                    else None
                ),
                idempotency_key=f"proposal:{turn.idempotency_key}",
            )
        return TurnResolution(
            appended_events=[turn.user_event, event],
            response=copy,
            context_diagnostics=context.diagnostics,
            pending_proposal=proposal,
        )

    async def _assemble_context(
        self,
        turn: ConversationTurn,
    ) -> ConversationWorkingContext:
        return await self._context_manager.assemble(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            current_user_message=turn.message,
            current_event_id=turn.user_event.event_id,
        )


def _current_intent_step(
    plan: ConversationIntentPlan,
) -> IntentExecutionStep | None:
    """Return the plan's current executable step."""
    return next(
        (
            step
            for step in plan.execution_plan.steps
            if step.status
            in {
                IntentStepStatus.READY,
                IntentStepStatus.READY_WITH_UNDO,
                IntentStepStatus.AWAITING_CONSENT,
                IntentStepStatus.ACTIVE,
            }
        ),
        None,
    )


def _primary_step(intent_result: IntentResult) -> IntentExecutionStep | None:
    plan = intent_result.execution_plan
    return plan.steps[0] if plan is not None and plan.steps else None
