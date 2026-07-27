"""Role-play must consume the shared conversation window without transcript duplication."""

from datetime import UTC, datetime

import pytest

from app.agents.roleplay import RoleplayAgent
from app.conversation.adapters.roleplay import RoleplayModuleAdapter
from app.db.repositories import InMemoryRoleplaySessionRepository
from app.memory.roleplay_store import RoleplaySessionStore
from app.models import RiskLevel, SafetyResult
from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ModuleRun,
    ModuleType,
    RoleplayParameters,
)
from app.models_conversation_context import (
    ConversationContextDiagnostics,
    ConversationContextProfile,
    ConversationWorkingContext,
)
from app.services.roleplay_service import RoleplayService


class CapturingLLMClient:
    """Capture the exact model input used by the unified role-play path."""

    def __init__(self) -> None:
        self.user_prompt = ""

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, temperature
        self.user_prompt = user_prompt
        return "我听到了。你希望我具体怎么配合？"


class LowSafety:
    """Keep the test on the ordinary module path."""

    async def classify(self, text: str) -> SafetyResult:
        del text
        return SafetyResult(risk_level=RiskLevel.LOW, reason="test")


class RecordingCheckpoint:
    """Record checkpoint calls without external persistence."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_roleplay(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_unified_roleplay_uses_timeline_and_stores_only_features() -> None:
    llm = CapturingLLMClient()
    service = RoleplayService(
        agent=RoleplayAgent(llm_client=llm),
        store=RoleplaySessionStore(
            repository=InMemoryRoleplaySessionRepository()
        ),
        safety_classifier=LowSafety(),  # type: ignore[arg-type]
        context_manager=object(),  # type: ignore[arg-type]
        checkpoint_service=RecordingCheckpoint(),  # type: ignore[arg-type]
        memory_retriever=object(),  # type: ignore[arg-type]
    )
    adapter = RoleplayModuleAdapter(service)
    run = ModuleRun(
        module_run_id="module-1",
        conversation_id="conversation-1",
        user_id="owner",
        module_type=ModuleType.ROLEPLAY,
        depth=1,
        module_parameters=RoleplayParameters(
            scenario_description="在小组讨论中表达不同意见",
            difficulty=3,
        ),
        started_at=datetime.now(UTC),
    )
    initial_context = _context(run, recent_events=[])
    started = await adapter.start(run, initial_context)
    run = run.model_copy(
        update={"domain_session_id": started.domain_session_id}
    )
    overlay = await adapter.build_overlay(run, initial_context)
    previous_turn = ConversationEvent(
        event_id="event-1",
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        sequence_no=1,
        event_type=ConversationEventType.MODULE_MESSAGE,
        role=ConversationEventRole.USER,
        content="我觉得可以先比较两个方案。",
        module_run_id=run.module_run_id,
        idempotency_key="event-1",
        created_at=datetime.now(UTC),
    )

    result = await adapter.handle_message(
        run,
        "我想补充一个不同看法。",
        _context(run, recent_events=[previous_turn]),
        overlay,
    )

    session = service.store.get_for_user(
        started.domain_session_id or "",
        run.user_id,
    )
    assert session is not None
    assert session.messages == []
    assert len(session.practice_features) == 1
    assert previous_turn.content in llm.user_prompt
    assert result.response == "我听到了。你希望我具体怎么配合？"


def _context(
    run: ModuleRun,
    *,
    recent_events: list[ConversationEvent],
) -> ConversationWorkingContext:
    return ConversationWorkingContext(
        conversation_id=run.conversation_id,
        current_user_message="我想补充一个不同看法。",
        recent_events=recent_events,
        active_module_stack=[run],
        diagnostics=ConversationContextDiagnostics(
            conversation_id_hash="0" * 16,
            recent_event_count=len(recent_events),
            active_module_count=1,
            selected_memory_count=0,
            estimated_tokens=128,
            total_token_budget=10_000,
            budget_profile=ConversationContextProfile.ROLEPLAY,
            tokenizer_backend="test",
        ),
    )
