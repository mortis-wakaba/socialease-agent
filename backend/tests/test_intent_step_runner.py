"""Unit tests for bounded sequential intent step execution."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.conversation.intent_step_runner import IntentStepRunner
from app.models import (
    Intent,
    IntentAuthorizationBasis,
    IntentExecutionPlan,
    IntentExecutionStep,
    IntentStepStatus,
    RiskLevel,
    SafetyResult,
)
from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
)
from app.models_conversation_context import (
    ConversationContextDiagnostics,
    ConversationWorkingContext,
)
from app.models_intent_plan import ConversationIntentPlan


class StepRepository:
    """Minimal repository fake for ordinary and module step transitions."""

    def __init__(self) -> None:
        self.events: list[ConversationEvent] = []
        self.plan_updates = 0
        self.stack_calls = 0

    async def list_module_stack(self, **values: object) -> list[object]:
        del values
        self.stack_calls += 1
        return []

    async def append_event(self, **values: object) -> ConversationEvent:
        event = ConversationEvent(
            event_id=f"event-{len(self.events) + 2}",
            sequence_no=len(self.events) + 2,
            created_at=datetime.now(UTC),
            **values,
        )
        self.events.append(event)
        return event

    async def update_intent_plan(
        self,
        plan: ConversationIntentPlan,
        *,
        expected_version: int,
    ) -> ConversationIntentPlan:
        assert plan.version == expected_version
        self.plan_updates += 1
        return plan.model_copy(update={"version": plan.version + 1})


class RecordingHarness:
    """Record trusted runner inputs and return one deterministic response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(self, request: object, **values: object) -> object:
        self.calls.append({"request": request, **values})
        return SimpleNamespace(response="先复盘刚才的进展。")


class ModuleCoordinator:
    """Return one module-start event without invoking a real adapter."""

    def __init__(self, event: ConversationEvent) -> None:
        self.event = event
        self.calls = 0

    async def accept(self, proposal: object, context: object) -> object:
        del proposal, context
        self.calls += 1
        return SimpleNamespace(
            appended_events=[self.event],
            response="角色扮演已开始。",
        )


async def _unexpected(**values: object) -> object:
    del values
    raise AssertionError("inactive runner dependency was invoked")


def _plan(intent: Intent) -> ConversationIntentPlan:
    now = datetime.now(UTC)
    return ConversationIntentPlan(
        plan_id="plan-1",
        conversation_id="conversation-1",
        user_id="user-1",
        source_event_id="event-1",
        execution_plan=IntentExecutionPlan(
            current_intent=intent,
            steps=[
                IntentExecutionStep(
                    step_id="step-1",
                    intent=intent,
                    authorization_basis=(
                        IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                    ),
                    status=IntentStepStatus.READY,
                )
            ],
        ),
        created_at=now,
        updated_at=now,
    )


def _source_event() -> ConversationEvent:
    return ConversationEvent(
        event_id="event-1",
        conversation_id="conversation-1",
        user_id="user-1",
        sequence_no=1,
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="先复盘，再开始角色扮演",
        idempotency_key="user:command-1",
        created_at=datetime.now(UTC),
    )


def _context() -> ConversationWorkingContext:
    return ConversationWorkingContext(
        conversation_id="conversation-1",
        current_user_message="先复盘，再开始角色扮演",
        diagnostics=ConversationContextDiagnostics(
            conversation_id_hash="0" * 16,
            recent_event_count=0,
            active_module_count=0,
            selected_memory_count=0,
            estimated_tokens=10,
            total_token_budget=100,
            tokenizer_backend="test",
        ),
    )


@pytest.mark.anyio
async def test_runner_abstains_before_dependencies_for_non_low_risk() -> None:
    plan = _plan(Intent.PROGRESS_REVIEW)
    runner = IntentStepRunner(
        repository=object(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        effect_executor=object(),  # type: ignore[arg-type]
        module_coordinator=object(),  # type: ignore[arg-type]
        create_proposal=_unexpected,  # type: ignore[arg-type]
        assemble_module_context=_unexpected,  # type: ignore[arg-type]
    )

    result = await runner.run(
        plan=plan,
        source_event=_source_event(),
        safety_result=SafetyResult(risk_level=RiskLevel.HIGH, reason="test"),
        context=_context(),
    )

    assert result.plan is plan
    assert result.events == []
    assert result.responses == []


@pytest.mark.anyio
async def test_runner_completes_ordinary_step_with_trusted_context() -> None:
    repository = StepRepository()
    harness = RecordingHarness()
    plan = _plan(Intent.PROGRESS_REVIEW)
    runner = IntentStepRunner(
        repository=repository,  # type: ignore[arg-type]
        harness=harness,  # type: ignore[arg-type]
        effect_executor=object(),  # type: ignore[arg-type]
        module_coordinator=object(),  # type: ignore[arg-type]
        create_proposal=_unexpected,  # type: ignore[arg-type]
        assemble_module_context=_unexpected,  # type: ignore[arg-type]
    )

    result = await runner.run(
        plan=plan,
        source_event=_source_event(),
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
        context=_context(),
    )

    assert result.plan.execution_plan.steps[0].status is IntentStepStatus.COMPLETED
    assert result.responses == ["先复盘刚才的进展。"]
    assert repository.plan_updates == 1
    assert len(harness.calls) == 1
    trusted_intent = harness.calls[0]["trusted_intent_result"]
    assert trusted_intent.intent is Intent.PROGRESS_REVIEW  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_runner_starts_module_and_stops_current_pass() -> None:
    repository = StepRepository()
    harness = RecordingHarness()
    module_event = ConversationEvent(
        event_id="event-2",
        conversation_id="conversation-1",
        user_id="user-1",
        sequence_no=2,
        event_type=ConversationEventType.MODULE_STARTED,
        role=ConversationEventRole.SYSTEM,
        content="角色扮演已开始。",
        structured_payload={
            "kind": "module_lifecycle",
            "module_run_id": "run-1",
            "module_type": "roleplay",
        },
        module_run_id="run-1",
        idempotency_key="module-start:step-1",
        created_at=datetime.now(UTC),
    )
    coordinator = ModuleCoordinator(module_event)

    async def create_proposal(**values: object) -> object:
        del values
        return SimpleNamespace(bounded_parameters=None)

    async def assemble_context(**values: object) -> ConversationWorkingContext:
        del values
        return _context()

    plan = _plan(Intent.ROLEPLAY_PRACTICE)
    runner = IntentStepRunner(
        repository=repository,  # type: ignore[arg-type]
        harness=harness,  # type: ignore[arg-type]
        effect_executor=object(),  # type: ignore[arg-type]
        module_coordinator=coordinator,  # type: ignore[arg-type]
        create_proposal=create_proposal,  # type: ignore[arg-type]
        assemble_module_context=assemble_context,
    )

    result = await runner.run(
        plan=plan,
        source_event=_source_event(),
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
        context=_context(),
    )

    assert result.plan.execution_plan.steps[0].status is IntentStepStatus.ACTIVE
    assert result.events == [module_event]
    assert result.responses == ["角色扮演已开始。"]
    assert coordinator.calls == 1
    assert harness.calls == []
