"""Contracts for encrypted, rebuildable Redis conversation-context caches."""

from datetime import UTC, datetime
import asyncio
import os
from uuid import uuid4

import pytest

from app.conversation.content_protector import (
    AESGCMConversationContentProtector,
    LocalPlaintextContentProtector,
)
from app.conversation.context_provider import (
    CachedConversationContextProvider,
    CachedConversationProjection,
    ProtectedConversationProjection,
)
from app.conversation.repository import SQLiteConversationRepository
from app.memory.task_state_store import (
    DisabledTaskStateStore,
    InMemoryTaskStateStore,
    RedisTaskStateStore,
    TaskStateStoreUnavailable,
)
from app.models_conversation import (
    ConversationEventRole,
    ConversationEventType,
)
from app.models_conversation_context import ConversationCompactSummary


@pytest.fixture
def anyio_backend() -> str:
    """Run async cache contracts on asyncio."""
    return "asyncio"


@pytest.fixture
def repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> SQLiteConversationRepository:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "context-cache.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    return SQLiteConversationRepository()


@pytest.mark.anyio
async def test_cache_hit_and_version_miss_match_database_projection(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = await repository.create(user_id="owner", title="Cached")
    await repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="第一条",
        idempotency_key="first",
    )
    provider = CachedConversationContextProvider(
        repository=repository,
        store=InMemoryTaskStateStore(),
        protector=LocalPlaintextContentProtector(),
    )

    first = await provider.load(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        recent_limit=32,
    )
    second = await provider.load(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        recent_limit=32,
    )
    await repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        event_type=ConversationEventType.ASSISTANT_MESSAGE,
        role=ConversationEventRole.ASSISTANT,
        content="第二条",
        idempotency_key="second",
    )
    rebuilt = await provider.load(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        recent_limit=32,
    )

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert rebuilt.cache_status == "miss"
    assert [event.content for event in rebuilt.recent_events] == [
        "第一条",
        "第二条",
    ]


@pytest.mark.anyio
async def test_cache_failure_falls_back_to_authoritative_database(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = await repository.create(user_id="owner", title="Degraded")
    provider = CachedConversationContextProvider(
        repository=repository,
        store=DisabledTaskStateStore(),
        protector=LocalPlaintextContentProtector(),
    )

    snapshot = await provider.load(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        recent_limit=32,
    )

    assert snapshot.conversation is not None
    assert snapshot.cache_status == "degraded"


@pytest.mark.anyio
async def test_concurrent_cache_misses_are_single_flight(
    repository: SQLiteConversationRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await repository.create(user_id="owner", title="Single flight")
    calls = 0
    original = repository.list_recent_events

    def counting_recent_events(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(repository, "list_recent_events", counting_recent_events)
    provider = CachedConversationContextProvider(
        repository=repository,
        store=InMemoryTaskStateStore(),
        protector=LocalPlaintextContentProtector(),
    )

    first, second = await asyncio.gather(
        provider.load(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            recent_limit=32,
        ),
        provider.load(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            recent_limit=32,
        ),
    )

    assert {first.cache_status, second.cache_status} == {"miss", "hit"}
    assert calls == 1


@pytest.mark.anyio
async def test_cache_outage_cannot_block_durable_deletion() -> None:
    class UnavailableDeleteStore(InMemoryTaskStateStore):
        async def delete(self, **kwargs: object) -> None:
            raise TaskStateStoreUnavailable("test")

        async def delete_user(self, **kwargs: object) -> int:
            raise TaskStateStoreUnavailable("test")

    provider = CachedConversationContextProvider(
        repository=None,  # type: ignore[arg-type]
        store=UnavailableDeleteStore(),
        protector=LocalPlaintextContentProtector(),
    )

    await provider.invalidate(conversation_id="conversation-1", user_id="owner")
    assert await provider.delete_user(user_id="owner") == 0


@pytest.mark.anyio
async def test_encrypted_cache_never_stores_plaintext_projection(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = await repository.create(user_id="owner", title="Encrypted")
    await repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="不应以明文进入缓存",
        idempotency_key="encrypted",
    )
    store: InMemoryTaskStateStore[ProtectedConversationProjection] = (
        InMemoryTaskStateStore()
    )
    provider = CachedConversationContextProvider(
        repository=repository,
        store=store,
        protector=AESGCMConversationContentProtector(
            key=b"k" * 32,
            key_version="test-v1",
        ),
    )

    await provider.load(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        recent_limit=32,
    )
    envelope = await store.get(
        user_id="owner",
        task_id=conversation.conversation_id,
    )

    assert envelope is not None
    assert envelope.plaintext is None
    assert envelope.ciphertext is not None
    assert "不应以明文进入缓存" not in envelope.model_dump_json()


@pytest.mark.anyio
async def test_owner_mismatched_cached_projection_is_rebuilt(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = await repository.create(user_id="owner", title="Scoped")
    store: InMemoryTaskStateStore[ProtectedConversationProjection] = (
        InMemoryTaskStateStore()
    )
    projection = CachedConversationProjection(
        event_watermark=conversation.version,
        recent_limit=32,
        recent_events=[],
        compact_summary=ConversationCompactSummary(
            conversation_id=conversation.conversation_id,
            user_id="other",
            current_topics=["不应跨 owner 使用"],
            updated_at=datetime.now(UTC),
        ),
        module_stack=[],
    )
    await store.put(
        user_id="owner",
        task_id=conversation.conversation_id,
        state=ProtectedConversationProjection(
            event_watermark=conversation.version,
            recent_limit=32,
            plaintext=projection.model_dump_json(),
            version=conversation.version,
        ),
        ttl_seconds=60,
    )
    provider = CachedConversationContextProvider(
        repository=repository,
        store=store,
        protector=LocalPlaintextContentProtector(),
    )

    snapshot = await provider.load(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        recent_limit=32,
    )

    assert snapshot.cache_status == "miss"
    assert snapshot.conversation is not None
    assert snapshot.compact_summary is None


@pytest.mark.anyio
@pytest.mark.redis_integration
async def test_real_redis_context_cache_round_trip_when_configured(
    repository: SQLiteConversationRepository,
) -> None:
    redis_url = os.getenv("SOCIALEASE_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("SOCIALEASE_TEST_REDIS_URL is required")
    user_id = f"redis-context-{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Redis")
    store = RedisTaskStateStore(
        redis_url=redis_url,
        namespace="conversation-context-test",
        model_type=ProtectedConversationProjection,
    )
    provider = CachedConversationContextProvider(
        repository=repository,
        store=store,
        protector=AESGCMConversationContentProtector(
            key=b"r" * 32,
            key_version="redis-test-v1",
        ),
        ttl_seconds=60,
    )
    try:
        first = await provider.load(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            recent_limit=32,
        )
        second = await provider.load(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            recent_limit=32,
        )
        assert first.cache_status == "miss"
        assert second.cache_status == "hit"
    finally:
        await store.delete_user(user_id=user_id)
        await provider.close()
