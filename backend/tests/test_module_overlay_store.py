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
from app.memory.task_state_store import (
    InMemoryTaskStateStore,
    RedisTaskStateStore,
    TaskStateStoreUnavailable,
)
from app.models_conversation import ModuleRun, RoleplayParameters, ModuleType
from app.models_module_overlay import ModuleOverlay, RoleplayOverlay


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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
