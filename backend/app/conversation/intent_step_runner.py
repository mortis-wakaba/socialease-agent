"""Run bounded sequential intent-plan steps outside the API service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.conversation.intent_effect_executor import IntentEffectExecutor
from app.conversation.intent_queue import (
    activate_current_ready_step,
    complete_current_intent_step,
)
from app.conversation.module_catalog import module_type_for_intent
from app.conversation.module_coordinator import ModuleCoordinator
from app.conversation.repository import ConversationRepository
from app.models import (
    ChatRequest,
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
    ModuleParameters,
    ModuleProposal,
    ModuleType,
)
from app.models_conversation_context import (
    ConversationWorkingContext,
    build_conversation_prompt_context,
)
from app.models_intent_plan import ConversationIntentPlan
from app.observability.turn_latency import measure_turn_stage
from app.workflow.engine import AgentHarness


class ProposalFactory(Protocol):
    """Persist one bounded module proposal for an authorized step."""

    async def __call__(
        self,
        *,
        conversation_id: str,
        user_id: str,
        source_event: ConversationEvent,
        message: str,
        module_type: ModuleType,
    ) -> ModuleProposal: ...


class ModuleContextAssembler(Protocol):
    """Assemble the bounded context required to start a module."""

    async def __call__(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        module_type: ModuleType,
        module_parameters: ModuleParameters | None = None,
        current_event_id: str | None = None,
    ) -> ConversationWorkingContext: ...


@dataclass(frozen=True)
class IntentStepRunResult:
    """Authoritative plan plus events produced by one bounded runner pass."""

    plan: ConversationIntentPlan
    events: list[ConversationEvent]
    responses: list[str]


class IntentStepRunner:
    """Advance reversible, module, or ordinary steps in strict sequence."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        harness: AgentHarness,
        effect_executor: IntentEffectExecutor,
        module_coordinator: ModuleCoordinator,
        create_proposal: ProposalFactory,
        assemble_module_context: ModuleContextAssembler,
    ) -> None:
        self._repository = repository
        self._harness = harness
        self._effect_executor = effect_executor
        self._module_coordinator = module_coordinator
        self._create_proposal = create_proposal
        self._assemble_module_context = assemble_module_context

    async def run(
        self,
        *,
        plan: ConversationIntentPlan,
        source_event: ConversationEvent,
        safety_result: SafetyResult,
        context: ConversationWorkingContext,
    ) -> IntentStepRunResult:
        """Advance at most two steps, stopping at every permission boundary."""
        events: list[ConversationEvent] = []
        responses: list[str] = []
        if safety_result.risk_level != RiskLevel.LOW:
            return IntentStepRunResult(plan=plan, events=events, responses=responses)

        for _ in range(2):
            current = _current_intent_step(plan)
            if current is None:
                break
            if current.status == IntentStepStatus.READY_WITH_UNDO:
                outcome = await self._effect_executor.execute(
                    plan=plan,
                    step=current,
                    source_event=source_event,
                )
                if outcome is None:
                    break
                plan = outcome.plan
                events.append(outcome.event)
                responses.append(outcome.response)
                if outcome.completed:
                    continue
                break
            if current.status != IntentStepStatus.READY:
                break
            if await self._has_active_module(plan):
                break

            module_type = module_type_for_intent(current.intent)
            if module_type is not None:
                plan, module_events, response = await self._start_module(
                    plan=plan,
                    step=current,
                    source_event=source_event,
                    module_type=module_type,
                )
                events.extend(module_events)
                responses.append(response)
                break

            plan, event, response = await self._run_ordinary_step(
                plan=plan,
                step=current,
                source_event=source_event,
                safety_result=safety_result,
                context=context,
            )
            events.append(event)
            responses.append(response)

        return IntentStepRunResult(plan=plan, events=events, responses=responses)

    async def _has_active_module(self, plan: ConversationIntentPlan) -> bool:
        stack = await self._repository.list_module_stack(
            conversation_id=plan.conversation_id,
            user_id=plan.user_id,
        )
        return bool(stack)

    async def _start_module(
        self,
        *,
        plan: ConversationIntentPlan,
        step: IntentExecutionStep,
        source_event: ConversationEvent,
        module_type: ModuleType,
    ) -> tuple[ConversationIntentPlan, list[ConversationEvent], str]:
        proposal = await self._create_proposal(
            conversation_id=plan.conversation_id,
            user_id=plan.user_id,
            source_event=source_event,
            message=source_event.content,
            module_type=module_type,
        )
        module_context = await self._assemble_module_context(
            conversation_id=plan.conversation_id,
            user_id=plan.user_id,
            message=source_event.content,
            module_type=module_type,
            module_parameters=proposal.bounded_parameters,
            current_event_id=source_event.event_id,
        )
        started = await self._module_coordinator.accept(proposal, module_context)
        activated = activate_current_ready_step(
            plan,
            expected_step_id=step.step_id,
        )
        if activated is not plan:
            plan = await self._repository.update_intent_plan(
                activated,
                expected_version=plan.version,
            )
        return plan, started.appended_events, started.response

    async def _run_ordinary_step(
        self,
        *,
        plan: ConversationIntentPlan,
        step: IntentExecutionStep,
        source_event: ConversationEvent,
        safety_result: SafetyResult,
        context: ConversationWorkingContext,
    ) -> tuple[ConversationIntentPlan, ConversationEvent, str]:
        with measure_turn_stage("workflow"):
            workflow = await self._harness.run(
                ChatRequest(
                    user_id=plan.user_id,
                    message=source_event.content,
                    context=conversation_workflow_context(
                        context,
                        source_event_id=source_event.event_id,
                    ),
                ),
                trusted_safety_result=safety_result,
                trusted_intent_result=IntentResult(
                    intent=step.intent,
                    confidence=1.0,
                    reason="authorized sequential plan progression",
                ),
                trusted_conversation_context=build_conversation_prompt_context(
                    context
                ),
                streaming_requested=False,
            )
        event = await self._repository.append_event(
            conversation_id=plan.conversation_id,
            user_id=plan.user_id,
            event_type=ConversationEventType.ASSISTANT_MESSAGE,
            role=ConversationEventRole.ASSISTANT,
            content=workflow.response,
            idempotency_key=f"intent-plan-step:{plan.plan_id}:{step.step_id}",
        )
        completed = complete_current_intent_step(
            plan,
            expected_intent=step.intent,
        )
        if completed is not plan:
            plan = await self._repository.update_intent_plan(
                completed,
                expected_version=plan.version,
            )
        return plan, event, workflow.response


def conversation_workflow_context(
    context: ConversationWorkingContext,
    *,
    source_event_id: str,
) -> dict[str, object]:
    """Project bounded conversation context into the existing harness."""
    return {
        "session_id": context.conversation_id,
        "request_id": source_event_id,
        "conversation_id": context.conversation_id,
        "module_stack": [
            {
                "module_type": run.module_type.value,
                "status": run.status.value,
                "depth": run.depth,
            }
            for run in context.active_module_stack
        ],
    }


def _current_intent_step(
    plan: ConversationIntentPlan,
) -> IntentExecutionStep | None:
    """Return the first nonterminal step from authoritative plan state."""
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
