"""Tests for encrypted, owner-scoped module overlay caching."""

from datetime import UTC, datetime
import os
from uuid import uuid4

import pytest

from app.conversation.content_protector import AESGCMConversationContentProtector
from app.conversation.module_overlay_store import (
    ModuleOverlayStore,
    ProtectedModuleOverlay,
)
from app.conversation.module_coordinator import ModuleCoordinator
from app.memory.task_state_store import (
    InMemoryTaskStateStore,
    RedisTaskStateStore,
    TaskStateStoreUnavailable,
)
from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ModuleRun,
    ModuleType,
    RoleplayMessageEventPayload,
    RoleplayParameters,
)
from app.models_conversation_context import (
    ConversationContextDiagnostics,
    ConversationWorkingContext,
)
from app.models_module_overlay import ModuleOverlay, RoleplayOverlay


class CountingTaskStateStore(InMemoryTaskStateStore[ProtectedModuleOverlay]):
    """Count overlay reads while retaining the in-memory store behavior."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    async def get(
        self,
        *,
        user_id: str,
        task_id: str,
    ) -> ProtectedModuleOverlay | None:
        self.get_calls += 1
        return await super().get(user_id=user_id, task_id=task_id)


class ReplayMessageRepository:
    """Minimal repository double for an idempotent module-message replay."""

    def __init__(self, run: ModuleRun, event: ConversationEvent) -> None:
        self.run = run
        self.event = event

    async def list_module_stack(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]:
        del conversation_id, user_id
        return [self.run]

    async def get_event_by_idempotency(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationEvent | None:
        del conversation_id, user_id, idempotency_key
        return self.event


@pytest.mark.anyio
async def test_overlay_cache_encrypts_text_and_enforces_owner_scope() -> None:
    backing: InMemoryTaskStateStore[ProtectedModuleOverlay] = (
        InMemoryTaskStateStore()
    )
    store = ModuleOverlayStore(
        store=backing,
        protector=AESGCMConversationContentProtector(
            key=b"x" * 32,
            key_version="test-v1",
        ),
    )
    run = _run()
    overlay = _overlay(run)

    await store.put(run, overlay)

    envelope = await backing.get(
        user_id=run.user_id,
        task_id=run.module_run_id,
    )
    assert envelope is not None
    assert envelope.plaintext is None
    assert "课堂讨论" not in envelope.model_dump_json()
    assert await store.get(run) == overlay
    assert await store.get(run.model_copy(update={"user_id": "other"})) is None


@pytest.mark.anyio
async def test_overlay_cache_tamper_is_a_safe_miss() -> None:
    backing: InMemoryTaskStateStore[ProtectedModuleOverlay] = (
        InMemoryTaskStateStore()
    )
    store = ModuleOverlayStore(
        store=backing,
        protector=AESGCMConversationContentProtector(
            key=b"x" * 32,
            key_version="test-v1",
        ),
    )
    run = _run()
    await store.put(run, _overlay(run))
    envelope = await backing.get(
        user_id=run.user_id,
        task_id=run.module_run_id,
    )
    assert envelope is not None
    await backing.put(
        user_id=run.user_id,
        task_id=run.module_run_id,
        state=envelope.model_copy(update={"ciphertext": "AAAA"}),
        ttl_seconds=60,
    )

    assert await store.get(run) is None


@pytest.mark.anyio
async def test_coordinator_projects_module_context_once_for_replay() -> None:
    backing = CountingTaskStateStore()
    store = ModuleOverlayStore(
        store=backing,
        protector=AESGCMConversationContentProtector(
            key=b"x" * 32,
            key_version="test-v1",
        ),
    )
    run = _run()
    await store.put(run, _overlay(run))
    replay = ConversationEvent(
        event_id="module-replay-event",
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        sequence_no=2,
        event_type=ConversationEventType.MODULE_MESSAGE,
        role=ConversationEventRole.ASSISTANT,
        content="已重放模块响应。",
        structured_payload=RoleplayMessageEventPayload(session_id="session-1"),
        module_run_id=run.module_run_id,
        idempotency_key="module-response:turn-1",
        created_at=datetime.now(UTC),
    )
    coordinator = ModuleCoordinator(
        repository=ReplayMessageRepository(run, replay),  # type: ignore[arg-type]
        adapters={},
        overlay_store=store,
    )
    context = ConversationWorkingContext(
        conversation_id=run.conversation_id,
        current_user_message="继续",
        diagnostics=ConversationContextDiagnostics(
            conversation_id_hash="0" * 16,
            recent_event_count=0,
            active_module_count=1,
            selected_memory_count=0,
            estimated_tokens=0,
            total_token_budget=7000,
            tokenizer_backend="test",
        ),
    )

    result, event, projected = await coordinator.handle_message(
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        message="继续",
        idempotency_key="turn-1",
        context=context,
    )

    assert result.response == replay.content
    assert event == replay
    assert projected.active_module_overlay is not None
    assert projected.active_module_overlay.module_run_id == run.module_run_id
    assert projected.diagnostics.active_overlay_type == ModuleType.ROLEPLAY.value
    assert backing.get_calls == 1


@pytest.mark.anyio
async def test_overlay_cache_outage_cannot_block_durable_deletion() -> None:
    class UnavailableDeleteStore(InMemoryTaskStateStore):
        async def delete(self, **kwargs: object) -> None:
            raise TaskStateStoreUnavailable("test")

        async def delete_user(self, **kwargs: object) -> int:
            raise TaskStateStoreUnavailable("test")

    store = ModuleOverlayStore(
        store=UnavailableDeleteStore(),
        protector=AESGCMConversationContentProtector(
            key=b"x" * 32,
            key_version="test-v1",
        ),
    )

    await store.delete(_run())
    assert await store.delete_user(user_id="owner") == 0


@pytest.mark.anyio
@pytest.mark.redis_integration
async def test_real_redis_overlay_round_trip_when_configured() -> None:
    redis_url = os.getenv("SOCIALEASE_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("SOCIALEASE_TEST_REDIS_URL is required")
    run = _run().model_copy(
        update={
            "module_run_id": f"module-{uuid4().hex}",
            "user_id": f"owner-{uuid4().hex}",
        }
    )
    backing = RedisTaskStateStore(
        redis_url=redis_url,
        namespace="module-overlay-test",
        model_type=ProtectedModuleOverlay,
    )
    store = ModuleOverlayStore(
        store=backing,
        protector=AESGCMConversationContentProtector(
            key=b"x" * 32,
            key_version="test-v1",
        ),
        ttl_seconds=60,
    )
    try:
        overlay = _overlay(run)
        await store.put(run, overlay)
        assert await store.get(run) == overlay
    finally:
        await store.delete_user(user_id=run.user_id)
        await store.close()


def _run() -> ModuleRun:
    return ModuleRun(
        module_run_id="module-1",
        conversation_id="conversation-1",
        user_id="owner",
        module_type=ModuleType.ROLEPLAY,
        depth=1,
        module_parameters=RoleplayParameters(
            scenario_description="课堂讨论",
        ),
        domain_session_id="session-1",
        started_at=datetime.now(UTC),
    )


def _overlay(run: ModuleRun) -> ModuleOverlay:
    return ModuleOverlay(
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        module_run_id=run.module_run_id,
        module_type=run.module_type,
        phase="active",
        payload=RoleplayOverlay(
            scenario_summary="课堂讨论",
            difficulty=2,
        ),
        version=run.version,
        updated_at=datetime.now(UTC),
    )
