"""Tests for consent-gated module proposals in unified conversations."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.conversation.compactor import ConversationCompactor
from app.conversation.context_manager import ConversationContextManager
from app.conversation.repository import SQLiteConversationRepository
from app.db.engine import connect
from app.models import (
    ChatResponse,
    Intent,
    IntentResult,
    RiskLevel,
    SafetyResult,
    TraceRecord,
)
from app.models_conversation import (
    HISTORY_NOTICE_VERSION,
    ModuleProposalStatus,
    ModuleType,
)
from app.services.conversation_service import (
    ConversationNoticeError,
    ConversationProposalError,
    ConversationService,
)


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
    ) -> ChatResponse:
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


@pytest.fixture
def repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> SQLiteConversationRepository:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "service.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    return SQLiteConversationRepository()


@pytest.fixture
def anyio_backend() -> str:
    """Run async conversation service tests on asyncio only."""
    return "asyncio"


def _service(
    repository: SQLiteConversationRepository,
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


def test_current_history_notice_is_required(
    repository: SQLiteConversationRepository,
) -> None:
    service = _service(repository)

    with pytest.raises(ConversationNoticeError):
        service.create_conversation(
            user_id="owner",
            title="Conversation",
            history_notice_version=HISTORY_NOTICE_VERSION,
            history_notice_acknowledged=False,
        )


@pytest.mark.anyio
async def test_module_intent_only_creates_an_option_until_user_confirms(
    repository: SQLiteConversationRepository,
) -> None:
    service = _service(repository)
    conversation = service.create_conversation(
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
    assert len(service.list_events(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        cursor=None,
        limit=20,
    ).items) == 2
    with connect() as connection:
        episodic_count = connection.execute(
            "SELECT COUNT(*) AS total FROM episodic_memories WHERE user_id = ?",
            ("owner",),
        ).fetchone()["total"]
        proposal_count = connection.execute(
            "SELECT COUNT(*) AS total FROM memory_proposals WHERE user_id = ?",
            ("owner",),
        ).fetchone()["total"]
    assert episodic_count == 0
    assert proposal_count == 0


@pytest.mark.anyio
async def test_crisis_preempts_proposal_and_module_routing(
    repository: SQLiteConversationRepository,
) -> None:
    service = _service(repository, crisis=True)
    conversation = service.create_conversation(
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
    assert "现实" not in response.response
    assert "紧急服务" in response.response
    assert response.appended_events[-1].event_type.value == "crisis_escalated"


@pytest.mark.anyio
async def test_proposal_reject_checks_hash_state_and_owner(
    repository: SQLiteConversationRepository,
) -> None:
    service = _service(repository)
    conversation = service.create_conversation(
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
        service.reject_proposal(
            conversation_id=conversation.conversation_id,
            proposal_id=proposal.proposal_id,
            user_id="owner",
            request_hash="b" * 64,
        )
    with pytest.raises(ConversationProposalError, match="not found"):
        service.reject_proposal(
            conversation_id=conversation.conversation_id,
            proposal_id=proposal.proposal_id,
            user_id="other",
            request_hash=proposal.request_hash,
        )
    rejected = service.reject_proposal(
        conversation_id=conversation.conversation_id,
        proposal_id=proposal.proposal_id,
        user_id="owner",
        request_hash=proposal.request_hash,
    )
    assert rejected.status is ModuleProposalStatus.REJECTED
    with pytest.raises(ConversationProposalError, match="no longer pending"):
        service.reject_proposal(
            conversation_id=conversation.conversation_id,
            proposal_id=proposal.proposal_id,
            user_id="owner",
            request_hash=proposal.request_hash,
        )


@pytest.mark.anyio
async def test_general_support_abstains_from_module_proposal(
    repository: SQLiteConversationRepository,
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
    conversation = service.create_conversation(
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
