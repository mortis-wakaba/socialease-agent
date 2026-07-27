"""Read bounded conversation state through database and Redis cache providers."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64DecodeError
import asyncio
from dataclasses import dataclass
import os
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.content_protector import (
    ConversationContentProtectionError,
    ConversationContentProtector,
    ProtectedContent,
    configured_content_protector,
)
from app.conversation.repository import ConversationRepository
from app.memory.runtime_requirements import task_state_runtime_report
from app.memory.redis_settings import redis_task_state_settings
from app.memory.task_state_store import (
    RedisTaskStateStore,
    TaskStateStore,
    TaskStateStoreUnavailable,
)
from app.models_conversation import Conversation, ConversationEvent, ModuleRun
from app.models_conversation_context import ConversationCompactSummary


@dataclass(frozen=True)
class ConversationContextSnapshot:
    """Authoritative inputs needed before prompt-context allocation."""

    conversation: Conversation | None
    recent_events: list[ConversationEvent]
    compact_summary: ConversationCompactSummary | None
    module_stack: list[ModuleRun]
    cache_status: str = "database"


class ConversationContextProvider(Protocol):
    """Load one owner-scoped context snapshot and invalidate cached projections."""

    backend_name: str

    async def load(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_limit: int,
    ) -> ConversationContextSnapshot: ...

    async def invalidate(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None: ...

    async def close(self) -> None: ...

    async def health(self) -> bool: ...


class DatabaseConversationContextProvider:
    """Read context inputs directly from the authoritative repository."""

    backend_name = "database"

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def load(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_limit: int,
    ) -> ConversationContextSnapshot:
        """Return one internally consistent owner-scoped read projection."""
        return ConversationContextSnapshot(
            conversation=self._repository.get_for_user(
                conversation_id,
                user_id,
            ),
            recent_events=self._repository.list_recent_events(
                conversation_id=conversation_id,
                user_id=user_id,
                limit=recent_limit,
            ),
            compact_summary=self._repository.get_compact_summary(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            module_stack=self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
        )

    async def invalidate(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Database reads have no projection cache to invalidate."""
        del conversation_id, user_id

    async def close(self) -> None:
        """Database repositories do not own an async cache client."""
        return None

    async def health(self) -> bool:
        """The database itself is covered by the main readiness probe."""
        return True


class CachedConversationProjection(BaseModel):
    """Decrypted cache body validated before it enters context assembly."""

    model_config = ConfigDict(extra="forbid")

    event_watermark: int = Field(ge=1)
    recent_limit: int = Field(ge=8, le=64)
    recent_events: list[ConversationEvent] = Field(max_length=64)
    compact_summary: ConversationCompactSummary | None = None
    module_stack: list[ModuleRun] = Field(max_length=3)


class ProtectedConversationProjection(BaseModel):
    """Encrypted Redis envelope with no directly indexed conversation text."""

    model_config = ConfigDict(extra="forbid")

    event_watermark: int = Field(ge=1)
    recent_limit: int = Field(ge=8, le=64)
    plaintext: str | None = None
    ciphertext: str | None = None
    nonce: str | None = None
    key_version: str | None = None
    version: int = Field(ge=1)


class CachedConversationContextProvider:
    """Cache database context projections without making Redis authoritative."""

    backend_name = "redis_cache"

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        store: TaskStateStore[ProtectedConversationProjection],
        protector: ConversationContentProtector,
        ttl_seconds: int = 3600,
    ) -> None:
        self._repository = repository
        self._database = DatabaseConversationContextProvider(repository)
        self._store = store
        self._protector = protector
        self._ttl_seconds = min(max(ttl_seconds, 60), 86_400)
        self._rebuild_locks = tuple(asyncio.Lock() for _ in range(64))

    async def load(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_limit: int,
    ) -> ConversationContextSnapshot:
        """Coalesce concurrent cache misses without affecting DB correctness."""
        lock = self._rebuild_locks[
            hash((user_id, conversation_id, recent_limit))
            % len(self._rebuild_locks)
        ]
        async with lock:
            return await self._load_projection(
                conversation_id=conversation_id,
                user_id=user_id,
                recent_limit=recent_limit,
            )

    async def _load_projection(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_limit: int,
    ) -> ConversationContextSnapshot:
        """Return a matching encrypted projection or rebuild from the database."""
        conversation = self._repository.get_for_user(conversation_id, user_id)
        if conversation is None:
            return ConversationContextSnapshot(
                conversation=None,
                recent_events=[],
                compact_summary=None,
                module_stack=[],
                cache_status="owner_miss",
            )
        try:
            envelope = await self._store.get(
                user_id=user_id,
                task_id=conversation_id,
            )
            if (
                envelope is not None
                and envelope.event_watermark == conversation.version
                and envelope.recent_limit == recent_limit
            ):
                projection = self._recover(
                    envelope,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                _validate_projection_owner(
                    projection,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                return ConversationContextSnapshot(
                    conversation=conversation,
                    recent_events=projection.recent_events,
                    compact_summary=projection.compact_summary,
                    module_stack=projection.module_stack,
                    cache_status="hit",
                )
        except (
            ConversationContentProtectionError,
            Base64DecodeError,
            TaskStateStoreUnavailable,
            ValueError,
        ):
            pass

        snapshot = await self._database.load(
            conversation_id=conversation_id,
            user_id=user_id,
            recent_limit=recent_limit,
        )
        projection = CachedConversationProjection(
            event_watermark=conversation.version,
            recent_limit=recent_limit,
            recent_events=snapshot.recent_events,
            compact_summary=snapshot.compact_summary,
            module_stack=snapshot.module_stack,
        )
        try:
            await self._store.put(
                user_id=user_id,
                task_id=conversation_id,
                state=self._protect(
                    projection,
                    conversation_id=conversation_id,
                    user_id=user_id,
                ),
                ttl_seconds=self._ttl_seconds,
            )
            cache_status = "miss"
        except TaskStateStoreUnavailable:
            cache_status = "degraded"
        return ConversationContextSnapshot(
            conversation=snapshot.conversation,
            recent_events=snapshot.recent_events,
            compact_summary=snapshot.compact_summary,
            module_stack=snapshot.module_stack,
            cache_status=cache_status,
        )

    async def invalidate(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Delete the exact conversation projection after same-version changes."""
        try:
            await self._store.delete(user_id=user_id, task_id=conversation_id)
        except TaskStateStoreUnavailable:
            return None

    async def delete_user(self, *, user_id: str) -> int:
        """Remove every cached projection for one user."""
        try:
            return await self._store.delete_user(user_id=user_id)
        except TaskStateStoreUnavailable:
            return 0

    async def close(self) -> None:
        """Close the owned Redis client."""
        await self._store.close()

    async def health(self) -> bool:
        """Return whether the configured Redis cache backend responds."""
        return await self._store.ping()

    def _protect(
        self,
        projection: CachedConversationProjection,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ProtectedConversationProjection:
        associated_data = _cache_associated_data(
            conversation_id,
            user_id,
            projection.event_watermark,
            projection.recent_limit,
        )
        protected = self._protector.protect(
            projection.model_dump_json(),
            associated_data=associated_data,
        )
        return ProtectedConversationProjection(
            event_watermark=projection.event_watermark,
            recent_limit=projection.recent_limit,
            plaintext=protected.plaintext,
            ciphertext=(
                urlsafe_b64encode(protected.ciphertext).decode("ascii")
                if protected.ciphertext is not None
                else None
            ),
            nonce=(
                urlsafe_b64encode(protected.nonce).decode("ascii")
                if protected.nonce is not None
                else None
            ),
            key_version=protected.key_version,
            version=projection.event_watermark,
        )

    def _recover(
        self,
        envelope: ProtectedConversationProjection,
        *,
        conversation_id: str,
        user_id: str,
    ) -> CachedConversationProjection:
        protected = ProtectedContent(
            plaintext=envelope.plaintext,
            ciphertext=(
                urlsafe_b64decode(envelope.ciphertext)
                if envelope.ciphertext is not None
                else None
            ),
            nonce=(
                urlsafe_b64decode(envelope.nonce)
                if envelope.nonce is not None
                else None
            ),
            key_version=envelope.key_version,
        )
        raw = self._protector.recover(
            protected,
            associated_data=_cache_associated_data(
                conversation_id,
                user_id,
                envelope.event_watermark,
                envelope.recent_limit,
            ),
        )
        return CachedConversationProjection.model_validate_json(raw)


def create_conversation_context_provider(
    repository: ConversationRepository,
) -> ConversationContextProvider:
    """Create a Redis cache provider when shared task state is configured."""
    report = task_state_runtime_report()
    if report.redis_url is None:
        return DatabaseConversationContextProvider(repository)
    settings = redis_task_state_settings()
    return CachedConversationContextProvider(
        repository=repository,
        store=RedisTaskStateStore(
            redis_url=report.redis_url,
            namespace="conversation-context",
            model_type=ProtectedConversationProjection,
            socket_timeout_seconds=settings.socket_timeout_seconds,
        ),
        protector=configured_content_protector(),
        ttl_seconds=_context_cache_ttl_seconds(),
    )


def _context_cache_ttl_seconds() -> int:
    try:
        value = int(os.getenv("CONVERSATION_CONTEXT_CACHE_TTL_SECONDS", "3600"))
    except ValueError:
        value = 3600
    return min(max(value, 60), 86_400)


def _cache_associated_data(
    conversation_id: str,
    user_id: str,
    event_watermark: int,
    recent_limit: int,
) -> bytes:
    return (
        f"conversation-context:{conversation_id}:{user_id}:"
        f"{event_watermark}:{recent_limit}"
    ).encode("utf-8")


def _validate_projection_owner(
    projection: CachedConversationProjection,
    *,
    conversation_id: str,
    user_id: str,
) -> None:
    scoped_items = [
        *projection.recent_events,
        *projection.module_stack,
    ]
    if projection.compact_summary is not None:
        scoped_items.append(projection.compact_summary)
    if any(
        item.conversation_id != conversation_id or item.user_id != user_id
        for item in scoped_items
    ):
        raise ValueError("cached conversation context owner mismatch")
