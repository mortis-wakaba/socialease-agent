"""Tests for authorization-aware modules and sequential conversation plans."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.conversation.compactor import ConversationCompactor
from app.conversation.context_manager import ConversationContextManager
from app.db.postgres.conversation_repository import PostgresConversationRepository
from app.db.repositories import InMemoryTraceRepository
from app.conversation.repository import (
    ConversationCommandClaim,
    ConversationIdempotencyError,
)
from app.models import (
    ChatResponse,
    Intent,
    IntentAuthorizationBasis,
    IntentExecutionPlan,
    IntentExecutionStep,
    IntentRelation,
    IntentResult,
    IntentStepStatus,
    RiskLevel,
    SafetyResult,
    TraceRecord,
)
from app.models_conversation import (
    ConversationEventType,
    ExposureParameters,
    HISTORY_NOTICE_VERSION,
    ModuleRun,
    ModuleProposalStatus,
    ModuleType,
    RoleplayParameters,
)
from app.models_calendar import (
    CalendarEventProposal,
    CalendarEventRecord,
    CalendarEventResponse,
    CalendarEventStatus,
    CalendarToolTrace,
)
from app.conversation.module_catalog import module_memory_request_context
from app.services.conversation_service import (
    ConversationCommandInProgressError,
    ConversationLeaseLostError,
    ConversationNoticeError,
    ConversationProposalError,
    ConversationService,
)
from app.services.conversation_runtime import build_conversation_service
from app.agents.support_generation import SupportGenerationAgent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.skills.registry import SkillRegistry
from app.skills.support import SupportSkill
from app.tracing.logger import TraceLogger
from app.conversation.reversible_actions import (
    ReversibleActionExecution,
    ReversibleActionPending,
)
from tests.postgres_test_support import fetch_one


class StubHarness:
    """Fail if a proposal test accidentally executes a domain skill."""

    async def run(self, *args: object, **kwargs: object):
        del args, kwargs
        raise AssertionError("the harness must not run before module confirmation")


class LowSafety:
    async def classify(self, message: str) -> SafetyResult:
        del message
        return SafetyResult(risk_level=RiskLevel.LOW, reason="test")


class CrisisSafety:
    async def classify(self, message: str) -> SafetyResult:
        del message
        return SafetyResult(risk_level=RiskLevel.CRISIS, reason="test")


class ConditionalCrisisSafety:
    """Classify only the explicit test marker as crisis."""

    async def classify(self, message: str) -> SafetyResult:
        return SafetyResult(
            risk_level=(
                RiskLevel.CRISIS if "crisis" in message else RiskLevel.LOW
            ),
            reason="test",
        )


class RoleplayIntent:
    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        return IntentResult(
            intent=Intent.ROLEPLAY_PRACTICE,
            confidence=0.95,
            reason="test",
        )


class ExplicitRoleplayIntent:
    """Return an application-authorized explicit module request."""

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        return IntentResult(
            intent=Intent.ROLEPLAY_PRACTICE,
            confidence=0.95,
            reason="explicit complete request",
            execution_plan=IntentExecutionPlan(
                current_intent=Intent.ROLEPLAY_PRACTICE,
                steps=[
                    IntentExecutionStep(
                        step_id="roleplay-primary",
                        intent=Intent.ROLEPLAY_PRACTICE,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY,
                    )
                ],
            ),
        )


class MultiGeneralIntent:
    """Return a two-step plan for service queue integration tests."""

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        return IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=0.95,
            reason="support then reminder",
            execution_plan=IntentExecutionPlan(
                current_intent=Intent.EMOTIONAL_SUPPORT,
                pending_intents=[Intent.CALENDAR_PLANNING],
                steps=[
                    IntentExecutionStep(
                        step_id="support-primary",
                        intent=Intent.EMOTIONAL_SUPPORT,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY,
                    ),
                    IntentExecutionStep(
                        step_id="calendar-secondary",
                        intent=Intent.CALENDAR_PLANNING,
                        relation=IntentRelation.AFTER_PRIMARY,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY_WITH_UNDO,
                        undo_supported=True,
                    ),
                ],
            ),
        )


class GeneralThenRoleplayIntent:
    """Return a ready one-shot step followed by a ready module."""

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        return IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=0.95,
            reason="support then practice",
            execution_plan=IntentExecutionPlan(
                current_intent=Intent.EMOTIONAL_SUPPORT,
                pending_intents=[Intent.ROLEPLAY_PRACTICE],
                steps=[
                    IntentExecutionStep(
                        step_id="support-first",
                        intent=Intent.EMOTIONAL_SUPPORT,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY,
                    ),
                    IntentExecutionStep(
                        step_id="roleplay-next",
                        intent=Intent.ROLEPLAY_PRACTICE,
                        relation=IntentRelation.AFTER_PRIMARY,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY,
                    ),
                ],
            ),
        )


class RoleplayThenReviewIntent:
    """Return a stateful module followed by a ready one-shot review."""

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        return IntentResult(
            intent=Intent.ROLEPLAY_PRACTICE,
            confidence=0.95,
            reason="practice then review",
            execution_plan=IntentExecutionPlan(
                current_intent=Intent.ROLEPLAY_PRACTICE,
                pending_intents=[Intent.PROGRESS_REVIEW],
                steps=[
                    IntentExecutionStep(
                        step_id="roleplay-first",
                        intent=Intent.ROLEPLAY_PRACTICE,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY,
                    ),
                    IntentExecutionStep(
                        step_id="review-next",
                        intent=Intent.PROGRESS_REVIEW,
                        relation=IntentRelation.AFTER_PRIMARY,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY,
                    ),
                ],
            ),
        )


class ExplicitCalendarIntent:
    """Return one explicit complete reversible calendar step."""

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        return IntentResult(
            intent=Intent.CALENDAR_PLANNING,
            confidence=0.98,
            reason="explicit schedule",
            execution_plan=IntentExecutionPlan(
                current_intent=Intent.CALENDAR_PLANNING,
                steps=[
                    IntentExecutionStep(
                        step_id="calendar-reversible",
                        intent=Intent.CALENDAR_PLANNING,
                        authorization_basis=(
                            IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                        ),
                        status=IntentStepStatus.READY_WITH_UNDO,
                        undo_supported=True,
                    )
                ],
            ),
        )


class FakeReversibleCalendarExecutor:
    """Return verified calendar results while recording compensation."""

    def __init__(self) -> None:
        self.created = 0
        self.undone = 0
        self.proposal: CalendarEventProposal | None = None

    async def create(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> ReversibleActionExecution:
        self.created += 1
        self.proposal = proposal
        return ReversibleActionExecution(
            job_id="create-job-1",
            response=self._response(
                user_id=user_id,
                idempotency_key=idempotency_key,
                status=CalendarEventStatus.CONFIRMED,
            ),
        )

    async def undo(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        idempotency_key: str,
    ) -> ReversibleActionExecution:
        del calendar_action_id
        self.undone += 1
        return ReversibleActionExecution(
            job_id="undo-job-1",
            response=self._response(
                user_id=user_id,
                idempotency_key=idempotency_key,
                status=CalendarEventStatus.CANCELLED,
            ),
        )

    def _response(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        status: CalendarEventStatus,
    ) -> CalendarEventResponse:
        now = datetime.now(UTC)
        assert self.proposal is not None
        return CalendarEventResponse(
            event=CalendarEventRecord(
                calendar_action_id="calendar-action-1",
                provider_event_id="provider-action-1",
                user_id=user_id,
                proposal=self.proposal,
                idempotency_key=idempotency_key,
                status=status,
                created_at=now,
                updated_at=now,
            ),
            verified=True,
            tool_trace=CalendarToolTrace(
                tool_name="calendar-test",
                transport="fake",
                status="success",
                latency_ms=0,
                read_after_write_verified=True,
            ),
        )


class PendingOnceCalendarExecutor(FakeReversibleCalendarExecutor):
    """Expose one durable pending create before succeeding on retry."""

    async def create(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> ReversibleActionExecution:
        if self.created == 0:
            self.created += 1
            self.proposal = proposal
            raise ReversibleActionPending("create-job-1")
        return await super().create(
            user_id=user_id,
            proposal=proposal,
            idempotency_key=idempotency_key,
        )


class GeneralIntent:
    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        return IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=0.8,
            reason="test",
        )


class ControlledSupportSkill:
    """Deterministic skill used while exercising the real Harness entry path."""

    descriptor = SkillDescriptor(
        name="general_support_skill",
        description="Controlled application-entry regression skill",
        supported_intents=(Intent.EMOTIONAL_SUPPORT,),
        entrypoint="tests.ControlledSupportSkill",
        safety_notes="test only",
    )

    async def run(self, context: SkillContext) -> SkillResult:
        del context
        return SkillResult(
            response="可以先从一句简短的开场开始。",
            structured_data={"agent": "support_agent", "action": "general_support"},
            selected_agent="support_agent",
        )


def test_module_memory_context_uses_validated_parameters_not_turn_text() -> None:
    roleplay = module_memory_request_context(
        ModuleType.ROLEPLAY,
        "我想补充一句",
        RoleplayParameters(
            scenario_description="小组讨论",
            difficulty=3,
        ),
    )
    exposure = module_memory_request_context(
        ModuleType.EXPOSURE,
        "完成了，先保持难度",
        ExposureParameters(goal="课堂发言", starting_anxiety=7),
    )

    assert roleplay == {"scenario": "小组讨论", "difficulty": 3}
    assert exposure == {
        "target_scenario": "课堂发言",
        "current_anxiety_level": 7,
    }


class GeneralHarness:
    def __init__(self) -> None:
        self.conversation_contexts = []

    async def run(
        self,
        request,
        *,
        trusted_safety_result: SafetyResult,
        trusted_intent_result: IntentResult,
        trusted_conversation_context,
        streaming_requested: bool = False,
    ) -> ChatResponse:
        del streaming_requested
        self.conversation_contexts.append(trusted_conversation_context)
        response = "可以先说说现在最困扰你的部分。"
        return ChatResponse(
            run_id="general-run",
            risk_level=trusted_safety_result.risk_level,
            intent=trusted_intent_result.intent,
            response=response,
            trace=TraceRecord(
                run_id="general-run",
                user_id=request.user_id,
                input="[minimized]",
                safety_result=trusted_safety_result,
                intent_result=trusted_intent_result,
                selected_agent="general_support",
                output=response,
                latency_ms=1,
                created_at=datetime.now(UTC),
            ),
        )


class BlockingGeneralHarness(GeneralHarness):
    """Hold one model call open so a concurrent replay can be asserted."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, *args: object, **kwargs: object) -> ChatResponse:
        self.entered.set()
        await self.release.wait()
        return await super().run(*args, **kwargs)


class FailingOnceGeneralHarness(GeneralHarness):
    """Fail one production boundary call before succeeding on the same command."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def run(self, *args: object, **kwargs: object) -> ChatResponse:
        if not self.failed:
            self.failed = True
            raise RuntimeError("controlled provider failure")
        return await super().run(*args, **kwargs)


class FailingSupportProvider:
    """Fail the real production support generation boundary deterministically."""

    async def generate_text(self, **kwargs: object) -> str:
        del kwargs
        raise RuntimeError("controlled support provider outage")


@pytest.fixture
def repository(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: str,
) -> PostgresConversationRepository:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    return PostgresConversationRepository(database_url=migrated_database)


def _service(
    repository: PostgresConversationRepository,
    *,
    crisis: bool = False,
) -> ConversationService:
    context_manager = ConversationContextManager(
        repository=repository,
        compactor=ConversationCompactor(),
    )
    return build_conversation_service(
        harness=StubHarness(),  # type: ignore[arg-type]
        repository=repository,
        safety_classifier=CrisisSafety() if crisis else LowSafety(),
        intent_router=RoleplayIntent(),
        context_manager=context_manager,
        proposal_ttl=timedelta(minutes=10),
    )


@pytest.mark.anyio
async def test_current_history_notice_is_required(
    repository: PostgresConversationRepository,
) -> None:
    service = _service(repository)

    with pytest.raises(ConversationNoticeError):
        await service.create_conversation(
            user_id="owner",
            title="Conversation",
            history_notice_version=HISTORY_NOTICE_VERSION,
            history_notice_acknowledged=False,
        )


@pytest.mark.anyio
async def test_module_intent_only_creates_an_option_until_user_confirms(
    repository: PostgresConversationRepository,
) -> None:
    service = _service(repository)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Practice",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    response = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="我想做角色扮演，练习小组讨论",
        idempotency_key="request-001",
    )

    assert response.pending_module_proposal is not None
    assert (
        response.pending_module_proposal.proposed_module
        is ModuleType.ROLEPLAY
    )
    assert response.active_module_stack == []
    assert response.workflow_response is None
    assert "确认前不会启动" in response.response

    replay = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="我想做角色扮演，练习小组讨论",
        idempotency_key="request-001",
    )
    assert (
        replay.pending_module_proposal.proposal_id
        == response.pending_module_proposal.proposal_id
    )
    assert len(
        (
            await service.list_events(
                conversation_id=conversation.conversation_id,
                user_id="owner",
                cursor=None,
                limit=20,
            )
        ).items
    ) == 2
    episodic_row = await fetch_one(
        "SELECT COUNT(*) AS total FROM episodic_memories WHERE user_id = :user_id",
        {"user_id": "owner"},
    )
    proposal_row = await fetch_one(
        "SELECT COUNT(*) AS total FROM memory_proposals WHERE user_id = :user_id",
        {"user_id": "owner"},
    )
    assert episodic_row is not None
    assert proposal_row is not None
    episodic_count = episodic_row["total"]
    proposal_count = proposal_row["total"]
    assert episodic_count == 0
    assert proposal_count == 0


@pytest.mark.anyio
async def test_explicit_complete_low_risk_module_starts_without_proposal_pause(
    repository: PostgresConversationRepository,
) -> None:
    service = _service(repository)
    service._intent_router = ExplicitRoleplayIntent()
    conversation = await service.create_conversation(
        user_id="owner",
        title="Direct practice",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    response = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="和我模拟一次小组讨论",
        idempotency_key="request-direct-module",
    )

    assert response.pending_module_proposal is None
    assert len(response.active_module_stack) == 1
    assert response.active_module_stack[0].module_type is ModuleType.ROLEPLAY


@pytest.mark.anyio
async def test_pending_queue_cancellation_preempts_ordinary_routing(
    repository: PostgresConversationRepository,
) -> None:
    harness = GeneralHarness()
    context_manager = ConversationContextManager(
        repository=repository,
        compactor=ConversationCompactor(),
    )
    router = MultiGeneralIntent()
    service = build_conversation_service(
        harness=harness,
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=router,
        context_manager=context_manager,
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Queue",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    first = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="先聊聊，之后设置提醒",
        idempotency_key="request-queue-create",
    )
    assert first.intent_plan is not None

    cancelled = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="当前继续，后面的都不用做了",
        idempotency_key="request-queue-cancel",
    )

    assert cancelled.intent_plan is not None
    assert cancelled.intent_plan.execution_plan.pending_intents == []
    assert (
        cancelled.intent_plan.execution_plan.steps[1].status
        is IntentStepStatus.CANCELLED
    )
    assert len(harness.conversation_contexts) == 1


@pytest.mark.anyio
async def test_completed_one_shot_step_starts_next_ready_module(
    repository: PostgresConversationRepository,
) -> None:
    harness = GeneralHarness()
    service = build_conversation_service(
        harness=harness,
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=GeneralThenRoleplayIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Auto progression",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    response = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="先听我说说，然后模拟小组讨论",
        idempotency_key="request-auto-progress",
    )

    assert response.intent_plan is not None
    assert [
        step.status for step in response.intent_plan.execution_plan.steps
    ] == [IntentStepStatus.COMPLETED, IntentStepStatus.ACTIVE]
    assert response.active_module_stack[-1].module_type is ModuleType.ROLEPLAY
    assert "接下来" in response.response


@pytest.mark.anyio
async def test_terminating_active_step_restores_source_and_runs_next_step(
    repository: PostgresConversationRepository,
) -> None:
    harness = GeneralHarness()
    service = build_conversation_service(
        harness=harness,
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=RoleplayThenReviewIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Delayed progression",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    started = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="先模拟小组讨论，再复盘",
        idempotency_key="request-delayed-progress",
    )
    run = started.active_module_stack[-1]

    finished = await service.terminate_current_module(
        conversation_id=conversation.conversation_id,
        module_run_id=run.module_run_id,
        user_id="owner",
    )

    assert finished.intent_plan is not None
    assert finished.intent_plan.status.value == "completed"
    assert all(
        step.status is IntentStepStatus.COMPLETED
        for step in finished.intent_plan.execution_plan.steps
    )
    assert "接下来" in finished.response
    assert len(harness.conversation_contexts) == 1


@pytest.mark.anyio
async def test_explicit_calendar_step_executes_and_can_be_undone(
    repository: PostgresConversationRepository,
) -> None:
    calendar = FakeReversibleCalendarExecutor()
    service = build_conversation_service(
        harness=GeneralHarness(),
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=ExplicitCalendarIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
        reversible_calendar_executor=calendar,  # type: ignore[arg-type]
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Reversible calendar",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    created = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="每天晚上8点提醒我练习",
        idempotency_key="request-calendar-create",
    )
    assert created.intent_plan is not None
    effect = created.intent_plan.execution_plan.steps[0].effect
    assert effect is not None
    assert effect.status.value == "applied"
    assert calendar.created == 1

    undone = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="取消提醒",
        idempotency_key="request-calendar-undo",
    )
    assert undone.intent_plan is not None
    undone_effect = undone.intent_plan.execution_plan.steps[0].effect
    assert undone_effect is not None
    assert undone_effect.status.value == "undone"
    assert calendar.undone == 1


@pytest.mark.anyio
async def test_pending_calendar_step_recovers_with_same_effect_receipt(
    repository: PostgresConversationRepository,
) -> None:
    calendar = PendingOnceCalendarExecutor()
    service = build_conversation_service(
        harness=GeneralHarness(),
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=ExplicitCalendarIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
        reversible_calendar_executor=calendar,  # type: ignore[arg-type]
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Pending calendar",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    pending = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="每天晚上8点提醒我练习",
        idempotency_key="request-calendar-pending",
    )
    recovered = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="每天晚上8点提醒我练习",
        idempotency_key="request-calendar-retry",
    )

    assert pending.intent_plan is not None
    assert (
        pending.intent_plan.execution_plan.steps[0].status
        is IntentStepStatus.READY_WITH_UNDO
    )
    assert "重试队列" in pending.response
    assert recovered.intent_plan is not None
    assert (
        recovered.intent_plan.execution_plan.steps[0].status
        is IntentStepStatus.COMPLETED
    )
    effect = await repository.latest_applied_intent_effect(
        conversation_id=conversation.conversation_id,
        user_id="owner",
    )
    assert effect is not None
    assert effect.status.value == "applied"
    assert effect.version == 2
    assert calendar.created == 2


@pytest.mark.anyio
async def test_crisis_preempts_proposal_and_module_routing(
    repository: PostgresConversationRepository,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Safety",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    response = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="request-002",
    )

    assert response.pending_module_proposal is None
    assert response.safety_result.risk_level is RiskLevel.CRISIS
    assert response.appended_events[0].event_type is ConversationEventType.CRISIS_INPUT
    assert "现实" not in response.response
    assert "紧急服务" in response.response
    assert response.appended_events[-1].event_type.value == "crisis_escalated"
    assert response.context_diagnostics.context_backend == "crisis_bypass"
    assert response.crisis_persistence_status == "persisted"


@pytest.mark.anyio
async def test_crisis_bypasses_broken_context_and_recovers_failed_preemption(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis isolation",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    await repository.create_module_run(
        ModuleRun(
            module_run_id=f"crisis-recovery-active-module-{uuid4().hex}",
            conversation_id=conversation.conversation_id,
            user_id="owner",
            module_type=ModuleType.ROLEPLAY,
            depth=1,
            module_parameters=RoleplayParameters(
                scenario_description="demo active module",
            ),
            started_at=datetime.now(UTC),
        )
    )

    async def context_must_not_run(**kwargs: object) -> object:
        del kwargs
        raise AssertionError("crisis must not assemble ordinary context")

    monkeypatch.setattr(service._context_manager, "assemble", context_must_not_run)
    original_preempt = service._module_coordinator.preempt_for_crisis

    async def fail_preemption(**kwargs: object) -> list[object]:
        del kwargs
        raise RuntimeError("controlled persistence outage")

    monkeypatch.setattr(
        service._module_coordinator,
        "preempt_for_crisis",
        fail_preemption,
    )
    arguments = {
        "conversation_id": conversation.conversation_id,
        "user_id": "owner",
        "message": "crisis test input",
        "idempotency_key": "crisis-recovery-001",
    }
    degraded = await service.send_message(**arguments)

    assert degraded.safety_result.risk_level is RiskLevel.CRISIS
    assert "紧急服务" in degraded.response
    assert degraded.crisis_persistence_status == "degraded_retry_required"
    recoverable = await service.get_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="crisis-recovery-001",
    )
    assert recoverable is not None
    assert recoverable.status == "recoverable"
    assert recoverable.safety_recovery_status == "recovery_pending"
    not_abandoned = await service.abandon_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="crisis-recovery-001",
    )
    assert not_abandoned is not None and not_abandoned.status == "recoverable"
    with pytest.raises(ConversationCommandInProgressError):
        await _service(repository).send_message(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            message="普通消息不应越过危机恢复",
            idempotency_key="ordinary-during-crisis-recovery-001",
        )
    with pytest.raises(ConversationCommandInProgressError):
        await service.terminate_all_modules(
            conversation_id=conversation.conversation_id,
            user_id="owner",
        )

    monkeypatch.setattr(
        service._module_coordinator,
        "preempt_for_crisis",
        original_preempt,
    )
    recovered = await service.send_message(**arguments)
    assert recovered.crisis_persistence_status == "persisted"
    assert recovered.active_module_stack == []
    following = await _service(repository).send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="安全恢复完成后继续普通消息",
        idempotency_key="ordinary-after-crisis-recovery-001",
    )
    assert following.response


@pytest.mark.anyio
async def test_lease_renewal_loss_cancels_business_execution(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = BlockingGeneralHarness()
    service = build_conversation_service(
        harness=harness,  # type: ignore[arg-type]
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=GeneralIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    service._command_lease_seconds = 0.3
    conversation = await service.create_conversation(
        user_id="owner",
        title="Lease loss",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    async def lose_lease(**kwargs: object) -> bool:
        del kwargs
        return False

    monkeypatch.setattr(repository, "renew_command_lease", lose_lease)
    with pytest.raises(ConversationLeaseLostError):
        await service.send_message(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            message="hold model",
            idempotency_key="lease-loss-001",
        )

    assert len(harness.conversation_contexts) == 0


@pytest.mark.anyio
async def test_crisis_lease_loss_still_returns_static_degraded_guidance(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    service._command_lease_seconds = 0.3
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis lease loss",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    async def blocked_preemption(**kwargs: object) -> list[object]:
        del kwargs
        await asyncio.Event().wait()
        return []

    async def lose_lease(**kwargs: object) -> bool:
        del kwargs
        return False

    monkeypatch.setattr(
        service._module_coordinator,
        "preempt_for_crisis",
        blocked_preemption,
    )
    monkeypatch.setattr(repository, "renew_command_lease", lose_lease)

    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="crisis-lease-loss-001",
    )

    assert result.crisis_persistence_status == "degraded_retry_required"
    assert result.context_diagnostics.context_backend == "crisis_bypass"
    assert "紧急服务" in result.response


@pytest.mark.anyio
async def test_crisis_final_lease_assertion_failure_returns_unconfirmed_guidance(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis final fence",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    async def fail_assertion(**kwargs: object) -> None:
        del kwargs
        raise ConversationLeaseLostError("controlled final lease loss")

    monkeypatch.setattr(repository, "assert_command_lease", fail_assertion)
    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="crisis-final-fence-001",
    )

    assert "紧急服务" in result.response
    assert result.crisis_persistence_status == (
        "safety_state_persisted_command_unconfirmed"
    )
    assert any(
        event.event_type is ConversationEventType.CRISIS_ESCALATED
        for event in result.appended_events
    )


@pytest.mark.anyio
async def test_injected_crisis_command_completion_cancellation_returns_guidance(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis completion cancellation",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    async def cancel_completion(**kwargs: object) -> None:
        del kwargs
        raise asyncio.CancelledError()

    monkeypatch.setattr(repository, "complete_command", cancel_completion)
    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="crisis-completion-cancel-001",
    )

    assert "紧急服务" in result.response
    assert result.crisis_persistence_status == (
        "safety_state_persisted_command_unconfirmed"
    )


@pytest.mark.anyio
async def test_completed_crisis_safety_retries_only_command_finalization(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis command finalization",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    original_complete = repository.complete_command
    original_preempt = service._module_coordinator.preempt_for_crisis
    completion_attempts = 0
    preemption_attempts = 0

    async def fail_first_completion(**kwargs: object) -> None:
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("controlled command completion failure")
        await original_complete(**kwargs)  # type: ignore[arg-type]

    async def count_preemption(**kwargs: object):
        nonlocal preemption_attempts
        preemption_attempts += 1
        return await original_preempt(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "complete_command", fail_first_completion)
    monkeypatch.setattr(
        service._module_coordinator,
        "preempt_for_crisis",
        count_preemption,
    )
    request = {
        "conversation_id": conversation.conversation_id,
        "user_id": "owner",
        "message": "crisis test input",
        "idempotency_key": "crisis-finalization-retry-001",
    }

    first = await service.send_message(**request)
    after_first = await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key=request["idempotency_key"],
    )
    second = await service.send_message(**request)
    third = await service.send_message(**request)
    final = await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key=request["idempotency_key"],
    )
    events = await service.list_events(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        cursor=None,
        limit=100,
    )

    assert first.crisis_persistence_status == (
        "safety_state_persisted_command_unconfirmed"
    )
    assert after_first is not None
    assert after_first.status == "recoverable"
    assert after_first.safety_recovery_status == "completed"
    assert second.crisis_persistence_status == "persisted"
    assert third == second
    assert final is not None
    assert final.status == "completed"
    assert final.safety_recovery_status == "completed"
    assert completion_attempts == 2
    assert preemption_attempts == 1
    assert sum(
        item.event_type is ConversationEventType.CRISIS_INPUT
        for item in events.items
    ) == 1
    assert sum(
        item.event_type is ConversationEventType.CRISIS_ESCALATED
        for item in events.items
    ) == 1


@pytest.mark.anyio
async def test_crisis_response_state_read_failure_returns_persisted_guidance(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis response read",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    original_get = repository.get_for_user
    calls = 0

    async def fail_second_read(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled response read failure")
        return await original_get(*args, **kwargs)

    monkeypatch.setattr(repository, "get_for_user", fail_second_read)
    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="crisis-response-read-001",
    )

    assert "紧急服务" in result.response
    assert result.crisis_persistence_status == "persisted"
    assert any(
        event.event_type is ConversationEventType.CRISIS_ESCALATED
        for event in result.appended_events
    )
    replayed = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="crisis-response-read-001",
    )
    assert replayed.model_dump() == result.model_dump()


@pytest.mark.anyio
async def test_injected_crisis_input_cancellation_returns_static_guidance(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis input cancellation",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    original_append = repository.append_event

    async def cancel_crisis_input(**kwargs: object):
        if kwargs["event_type"] is ConversationEventType.CRISIS_INPUT:
            raise asyncio.CancelledError()
        return await original_append(**kwargs)

    monkeypatch.setattr(repository, "append_event", cancel_crisis_input)
    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="crisis-input-cancel-001",
    )

    assert "紧急服务" in result.response
    assert result.crisis_persistence_status == "degraded_retry_required"


@pytest.mark.anyio
async def test_real_task_cancellation_propagates_and_releases_command(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis shutdown cancellation",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    await repository.claim_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="ordinary-before-cancelled-crisis",
        request_hash="d" * 64,
        lease_owner="ordinary-worker",
        lease_seconds=60,
    )
    await repository.record_command_safety_classification(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="ordinary-before-cancelled-crisis",
        lease_owner="ordinary-worker",
        crisis=False,
    )
    original_get = repository.get_for_user
    entered = asyncio.Event()

    async def block_post_preemption_read(*args: object, **kwargs: object):
        del args, kwargs
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(repository, "get_for_user", block_post_preemption_read)
    task = asyncio.create_task(
        service.send_message(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            message="crisis test input",
            idempotency_key="crisis-real-cancel-001",
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(repository, "get_for_user", original_get)
    command = await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="crisis-real-cancel-001",
    )
    assert command is not None
    assert command.status == "recoverable"
    assert command.safety_recovery_status == "classification_pending"


@pytest.mark.anyio
async def test_failed_normal_command_retries_same_key_then_allows_new_message(
    repository: PostgresConversationRepository,
) -> None:
    harness = FailingOnceGeneralHarness()
    service = build_conversation_service(
        harness=harness,  # type: ignore[arg-type]
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=GeneralIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Same-key retry",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    arguments = {
        "conversation_id": conversation.conversation_id,
        "user_id": "owner",
        "message": "同一条待恢复消息",
        "idempotency_key": "same-key-provider-retry-001",
    }

    with pytest.raises(RuntimeError, match="controlled provider failure"):
        await service.send_message(**arguments)
    recoverable = await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key=arguments["idempotency_key"],
    )
    assert recoverable is not None and recoverable.status == "recoverable"
    recovered = await service.send_message(**arguments)
    replay = await service.send_message(**arguments)
    following = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="恢复后发送的新消息",
        idempotency_key="after-recovery-message-001",
    )

    assert recovered.model_dump() == replay.model_dump()
    assert following.response
    events = await repository.list_recent_events(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        limit=20,
    )
    assert sum(event.idempotency_key == "user:same-key-provider-retry-001" for event in events) == 1


@pytest.mark.anyio
async def test_crisis_message_preempts_unfinished_normal_command(
    repository: PostgresConversationRepository,
) -> None:
    harness = BlockingGeneralHarness()
    service = build_conversation_service(
        harness=harness,  # type: ignore[arg-type]
        repository=repository,
        safety_classifier=ConditionalCrisisSafety(),
        intent_router=GeneralIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Crisis priority",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    ordinary = asyncio.create_task(
        service.send_message(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            message="hold ordinary model",
            idempotency_key="ordinary-before-crisis-001",
        )
    )
    await harness.entered.wait()

    crisis = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key="priority-crisis-001",
    )
    harness.release.set()
    with pytest.raises(Exception, match="lease"):
        await ordinary

    assert crisis.safety_result.risk_level is RiskLevel.CRISIS
    assert "紧急服务" in crisis.response
    old = await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="ordinary-before-crisis-001",
    )
    assert old is not None and old.status == "abandoned"


@pytest.mark.anyio
@pytest.mark.parametrize("priority_failure", ["exception", "not_acquired"])
async def test_busy_crisis_claim_failure_returns_static_degraded_guidance(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
    priority_failure: str,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Busy crisis fallback",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    await repository.claim_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="existing-normal-command",
        request_hash="a" * 64,
        lease_owner="existing-worker",
        lease_seconds=60,
    )
    await repository.record_command_safety_classification(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="existing-normal-command",
        lease_owner="existing-worker",
        crisis=False,
    )
    original_claim = repository.claim_command

    async def fail_priority_claim(**kwargs: object) -> ConversationCommandClaim:
        if kwargs.get("preempt_active"):
            if priority_failure == "exception":
                raise RuntimeError("controlled preemption database failure")
            return ConversationCommandClaim(acquired=False)
        return await original_claim(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "claim_command", fail_priority_claim)
    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="crisis test input",
        idempotency_key=f"busy-crisis-{priority_failure}",
    )

    assert result.safety_result.risk_level is RiskLevel.CRISIS
    assert "紧急服务" in result.response
    assert result.crisis_persistence_status == "degraded_retry_required"
    assert result.appended_events == []


@pytest.mark.anyio
async def test_busy_crisis_priority_claim_propagates_external_cancellation(
    repository: PostgresConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(repository, crisis=True)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Busy crisis cancellation",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    await repository.claim_command(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="existing-cancel-command",
        request_hash="c" * 64,
        lease_owner="existing-cancel-worker",
        lease_seconds=60,
    )
    await repository.record_command_safety_classification(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        idempotency_key="existing-cancel-command",
        lease_owner="existing-cancel-worker",
        crisis=False,
    )
    original_claim = repository.claim_command
    entered = asyncio.Event()

    async def block_priority_claim(**kwargs: object) -> ConversationCommandClaim:
        if kwargs.get("preempt_active"):
            entered.set()
            await asyncio.Event().wait()
        return await original_claim(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "claim_command", block_priority_claim)
    task = asyncio.create_task(
        service.send_message(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            message="crisis test input",
            idempotency_key="busy-crisis-real-cancel",
        )
    )
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_proposal_reject_checks_hash_state_and_owner(
    repository: PostgresConversationRepository,
) -> None:
    service = _service(repository)
    conversation = await service.create_conversation(
        user_id="owner",
        title="Decision",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    response = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="角色扮演练习",
        idempotency_key="request-003",
    )
    proposal = response.pending_module_proposal
    assert proposal is not None

    with pytest.raises(ConversationProposalError, match="hash"):
        await service.reject_proposal(
            conversation_id=conversation.conversation_id,
            proposal_id=proposal.proposal_id,
            user_id="owner",
            request_hash="b" * 64,
        )
    with pytest.raises(ConversationProposalError, match="not found"):
        await service.reject_proposal(
            conversation_id=conversation.conversation_id,
            proposal_id=proposal.proposal_id,
            user_id="other",
            request_hash=proposal.request_hash,
        )
    rejected = await service.reject_proposal(
        conversation_id=conversation.conversation_id,
        proposal_id=proposal.proposal_id,
        user_id="owner",
        request_hash=proposal.request_hash,
    )
    assert rejected.status is ModuleProposalStatus.REJECTED
    with pytest.raises(ConversationProposalError, match="no longer pending"):
        await service.reject_proposal(
            conversation_id=conversation.conversation_id,
            proposal_id=proposal.proposal_id,
            user_id="owner",
            request_hash=proposal.request_hash,
        )


@pytest.mark.anyio
async def test_general_support_abstains_from_module_proposal(
    repository: PostgresConversationRepository,
) -> None:
    harness = GeneralHarness()
    service = build_conversation_service(
        harness=harness,  # type: ignore[arg-type]
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=GeneralIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="General",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    response = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="今天和同学聊完后有点失落",
        idempotency_key="general-support-001",
    )

    assert response.pending_module_proposal is None
    assert response.active_module_stack == []
    assert response.response == "可以先说说现在最困扰你的部分。"
    assert [event.event_type.value for event in response.appended_events] == [
        "user_message",
        "assistant_message",
    ]
    assert harness.conversation_contexts[0].recent_events == []

    await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="我想接着聊刚才的失落感",
        idempotency_key="general-support-002",
    )

    historical_events = harness.conversation_contexts[1].recent_events
    assert [event.content for event in historical_events] == [
        "今天和同学聊完后有点失落",
        "可以先说说现在最困扰你的部分。",
    ]


@pytest.mark.anyio
async def test_application_entry_runs_real_conversation_service_and_harness(
    repository: PostgresConversationRepository,
    isolated_harness_factory,
) -> None:
    harness = isolated_harness_factory(
        skill_registry=SkillRegistry(executable_skills=(ControlledSupportSkill(),)),
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
    )
    service = build_conversation_service(
        harness=harness,
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=GeneralIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Application entry",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="明天小组讨论前有些紧张",
        idempotency_key="application-entry-001",
    )

    assert result.response == "可以先从一句简短的开场开始。"
    assert result.workflow_response is not None
    assert result.workflow_response.trace.selected_skill == "general_support_skill"
    assert [event.event_type for event in result.appended_events] == [
        ConversationEventType.USER_MESSAGE,
        ConversationEventType.ASSISTANT_MESSAGE,
    ]


@pytest.mark.anyio
async def test_real_support_skill_provider_failure_falls_back_through_application_entry(
    repository: PostgresConversationRepository,
    isolated_harness_factory,
) -> None:
    support_skill = SupportSkill(
        generation_agent=SupportGenerationAgent(
            llm_client=FailingSupportProvider(),  # type: ignore[arg-type]
        )
    )
    harness = isolated_harness_factory(
        skill_registry=SkillRegistry(executable_skills=(support_skill,)),
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
    )
    service = build_conversation_service(
        harness=harness,
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=GeneralIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Real support failure",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    result = await service.send_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="小组发言前有些紧张",
        idempotency_key="real-support-provider-failure-001",
    )

    assert result.workflow_response is not None
    assert result.workflow_response.trace.selected_skill == "general_support_skill"
    assert result.workflow_response.structured_data["fallback_used"] is True
    assert result.workflow_response.structured_data["fallback_reason"] == (
        "PROVIDER_ERROR"
    )
    assert result.response


@pytest.mark.anyio
async def test_message_command_prevents_concurrent_model_execution_and_replays(
    repository: PostgresConversationRepository,
) -> None:
    harness = BlockingGeneralHarness()
    service = build_conversation_service(
        harness=harness,  # type: ignore[arg-type]
        repository=repository,
        safety_classifier=LowSafety(),
        intent_router=GeneralIntent(),
        context_manager=ConversationContextManager(
            repository=repository,
            compactor=ConversationCompactor(),
        ),
    )
    conversation = await service.create_conversation(
        user_id="owner",
        title="Concurrent",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )
    arguments = {
        "conversation_id": conversation.conversation_id,
        "user_id": "owner",
        "message": "同一条消息",
        "idempotency_key": "concurrent-command-001",
    }

    first = asyncio.create_task(service.send_message(**arguments))
    await harness.entered.wait()
    with pytest.raises(ConversationCommandInProgressError):
        await service.send_message(**arguments)
    with pytest.raises(ConversationIdempotencyError):
        await service.send_message(**{**arguments, "message": "不同消息"})
    harness.release.set()
    completed = await first
    replay = await service.send_message(**arguments)

    assert replay == completed
    assert len(harness.conversation_contexts) == 1
    assert len(
        (
            await service.list_events(
                conversation_id=conversation.conversation_id,
                user_id="owner",
                cursor=None,
                limit=20,
            )
        ).items
    ) == 2
