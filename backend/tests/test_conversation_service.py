"""Tests for authorization-aware modules and sequential conversation plans."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.conversation.compactor import ConversationCompactor
from app.conversation.context_manager import ConversationContextManager
from app.db.postgres.conversation_repository import PostgresConversationRepository
from app.conversation.repository import ConversationIdempotencyError
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
    ConversationNoticeError,
    ConversationProposalError,
    ConversationService,
)
from app.conversation.reversible_actions import ReversibleActionExecution
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
    return ConversationService(
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
    service = ConversationService(
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
    service = ConversationService(
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
    service = ConversationService(
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
    service = ConversationService(
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
    service = ConversationService(
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
async def test_message_command_prevents_concurrent_model_execution_and_replays(
    repository: PostgresConversationRepository,
) -> None:
    harness = BlockingGeneralHarness()
    service = ConversationService(
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
