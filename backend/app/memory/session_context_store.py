"""Short-lived role-play context stores backed by Redis or test memory."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.models_roleplay import RoleplayMessageRole
from app.models_session_context import (
    RoleplaySessionContext,
    SessionContextMessage,
)
from app.memory.task_state_store import (
    RedisTaskStateStore,
    TaskStateStoreUnavailable,
)


class SessionContextStoreUnavailable(RuntimeError):
    """Raised when the configured short-term context backend cannot be used."""


class SessionContextStore(Protocol):
    """Async persistence contract for short-lived raw role-play context."""

    backend_name: str

    async def initialize(
        self,
        *,
        user_id: str,
        session_id: str,
        opening_message: str,
        ttl_seconds: int,
    ) -> RoleplaySessionContext: ...

    async def append_message(
        self,
        *,
        user_id: str,
        session_id: str,
        role: RoleplayMessageRole,
        content: str,
        ttl_seconds: int,
    ) -> RoleplaySessionContext: ...

    async def get(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> RoleplaySessionContext | None: ...

    async def replace(
        self,
        *,
        context: RoleplaySessionContext,
        expected_version: int,
        ttl_seconds: int,
    ) -> bool: ...

    async def refresh_ttl(
        self,
        *,
        user_id: str,
        session_id: str,
        ttl_seconds: int,
    ) -> bool: ...

    async def delete(self, *, user_id: str, session_id: str) -> None: ...
    async def delete_user(self, *, user_id: str) -> int: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class DisabledSessionContextStore:
    """Explicit degraded backend used when no Redis URL is configured."""

    backend_name = "disabled"

    async def initialize(self, **kwargs: object) -> RoleplaySessionContext:
        del kwargs
        raise SessionContextStoreUnavailable("Redis session context is not configured.")

    async def append_message(self, **kwargs: object) -> RoleplaySessionContext:
        del kwargs
        raise SessionContextStoreUnavailable("Redis session context is not configured.")

    async def get(self, **kwargs: object) -> RoleplaySessionContext | None:
        del kwargs
        raise SessionContextStoreUnavailable("Redis session context is not configured.")

    async def replace(self, **kwargs: object) -> bool:
        del kwargs
        raise SessionContextStoreUnavailable("Redis session context is not configured.")

    async def refresh_ttl(self, **kwargs: object) -> bool:
        del kwargs
        raise SessionContextStoreUnavailable("Redis session context is not configured.")

    async def delete(self, **kwargs: object) -> None:
        del kwargs
        return None

    async def delete_user(self, **kwargs: object) -> int:
        del kwargs
        return 0

    async def ping(self) -> bool:
        return False

    async def close(self) -> None:
        return None


class InMemorySessionContextStore:
    """Deterministic async store for tests; production does not select it."""

    backend_name = "memory_test_double"

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._contexts: dict[tuple[str, str], RoleplaySessionContext] = {}
        self._expires_at: dict[tuple[str, str], datetime] = {}
        self._lock = asyncio.Lock()

    async def initialize(
        self,
        *,
        user_id: str,
        session_id: str,
        opening_message: str,
        ttl_seconds: int,
    ) -> RoleplaySessionContext:
        async with self._lock:
            now = self._now()
            context = RoleplaySessionContext(
                user_id=user_id,
                session_id=session_id,
                messages=[
                    SessionContextMessage(
                        role=RoleplayMessageRole.AGENT,
                        content=opening_message,
                        created_at=now,
                    )
                ],
                updated_at=now,
            )
            self._save(context, ttl_seconds)
            return context.model_copy(deep=True)

    async def append_message(
        self,
        *,
        user_id: str,
        session_id: str,
        role: RoleplayMessageRole,
        content: str,
        ttl_seconds: int,
    ) -> RoleplaySessionContext:
        async with self._lock:
            current = self._get_live(user_id, session_id)
            now = self._now()
            if current is None:
                current = RoleplaySessionContext(
                    user_id=user_id,
                    session_id=session_id,
                    updated_at=now,
                )
            updated = current.model_copy(
                update={
                    "messages": [
                        *current.messages,
                        SessionContextMessage(role=role, content=content, created_at=now),
                    ][-64:],
                    "version": current.version + 1,
                    "updated_at": now,
                },
                deep=True,
            )
            self._save(updated, ttl_seconds)
            return updated.model_copy(deep=True)

    async def get(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> RoleplaySessionContext | None:
        async with self._lock:
            context = self._get_live(user_id, session_id)
            return context.model_copy(deep=True) if context is not None else None

    async def replace(
        self,
        *,
        context: RoleplaySessionContext,
        expected_version: int,
        ttl_seconds: int,
    ) -> bool:
        async with self._lock:
            current = self._get_live(context.user_id, context.session_id)
            if current is None or current.version != expected_version:
                return False
            updated = context.model_copy(
                update={
                    "version": expected_version + 1,
                    "updated_at": self._now(),
                },
                deep=True,
            )
            self._save(updated, ttl_seconds)
            return True

    async def refresh_ttl(
        self,
        *,
        user_id: str,
        session_id: str,
        ttl_seconds: int,
    ) -> bool:
        async with self._lock:
            context = self._get_live(user_id, session_id)
            if context is None:
                return False
            self._expires_at[(user_id, session_id)] = self._now() + timedelta(
                seconds=ttl_seconds
            )
            return True

    async def delete(self, *, user_id: str, session_id: str) -> None:
        async with self._lock:
            key = (user_id, session_id)
            self._contexts.pop(key, None)
            self._expires_at.pop(key, None)

    async def delete_user(self, *, user_id: str) -> int:
        async with self._lock:
            keys = [key for key in self._contexts if key[0] == user_id]
            for key in keys:
                self._contexts.pop(key, None)
                self._expires_at.pop(key, None)
            return len(keys)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    def _save(self, context: RoleplaySessionContext, ttl_seconds: int) -> None:
        key = (context.user_id, context.session_id)
        self._contexts[key] = context.model_copy(deep=True)
        self._expires_at[key] = self._now() + timedelta(seconds=ttl_seconds)

    def _get_live(self, user_id: str, session_id: str) -> RoleplaySessionContext | None:
        key = (user_id, session_id)
        expires_at = self._expires_at.get(key)
        if expires_at is not None and expires_at <= self._now():
            self._contexts.pop(key, None)
            self._expires_at.pop(key, None)
            return None
        return self._contexts.get(key)


class RedisSessionContextStore:
    """Redis-backed TTL store using optimistic updates for each session payload."""

    backend_name = "redis"

    def __init__(
        self,
        *,
        redis_url: str,
        socket_timeout_seconds: float = 0.5,
        key_prefix: str = "socialease:roleplay-context",
    ) -> None:
        self._state_store = RedisTaskStateStore(
            redis_url=redis_url,
            namespace=key_prefix.removeprefix("socialease:"),
            model_type=RoleplaySessionContext,
            socket_timeout_seconds=socket_timeout_seconds,
        )

    async def initialize(
        self,
        *,
        user_id: str,
        session_id: str,
        opening_message: str,
        ttl_seconds: int,
    ) -> RoleplaySessionContext:
        now = datetime.now(timezone.utc)
        context = RoleplaySessionContext(
            user_id=user_id,
            session_id=session_id,
            messages=[
                SessionContextMessage(
                    role=RoleplayMessageRole.AGENT,
                    content=opening_message,
                    created_at=now,
                )
            ],
            updated_at=now,
        )
        try:
            await self._state_store.put(
                user_id=user_id,
                task_id=session_id,
                state=context,
                ttl_seconds=ttl_seconds,
            )
            return context
        except Exception as exc:
            if isinstance(exc, SessionContextStoreUnavailable):
                raise
            raise SessionContextStoreUnavailable(
                f"Redis initialization failed: {exc.__class__.__name__}"
            ) from exc

    async def append_message(
        self,
        *,
        user_id: str,
        session_id: str,
        role: RoleplayMessageRole,
        content: str,
        ttl_seconds: int,
    ) -> RoleplaySessionContext:
        for _attempt in range(3):
            try:
                now = datetime.now(timezone.utc)
                current = await self._state_store.get(
                    user_id=user_id,
                    task_id=session_id,
                )
                if current is None:
                    current = RoleplaySessionContext(
                        user_id=user_id, session_id=session_id, updated_at=now
                    )
                    await self._state_store.put(
                        user_id=user_id,
                        task_id=session_id,
                        state=current,
                        ttl_seconds=ttl_seconds,
                    )
                updated = current.model_copy(
                    update={
                        "messages": [
                            *current.messages,
                            SessionContextMessage(
                                role=role,
                                content=content,
                                created_at=now,
                            ),
                        ][-64:],
                        "version": current.version + 1,
                        "updated_at": now,
                    },
                    deep=True,
                )
                if await self._state_store.compare_and_set(
                    user_id=user_id,
                    task_id=session_id,
                    state=updated,
                    expected_version=current.version,
                    ttl_seconds=ttl_seconds,
                ):
                    return updated
            except TaskStateStoreUnavailable as exc:
                raise SessionContextStoreUnavailable(str(exc)) from exc
        raise SessionContextStoreUnavailable("Redis append conflicted repeatedly.")

    async def get(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> RoleplaySessionContext | None:
        try:
            return await self._state_store.get(user_id=user_id, task_id=session_id)
        except Exception as exc:
            raise SessionContextStoreUnavailable(
                f"Redis read failed: {exc.__class__.__name__}"
            ) from exc

    async def replace(
        self,
        *,
        context: RoleplaySessionContext,
        expected_version: int,
        ttl_seconds: int,
    ) -> bool:
        try:
            updated = context.model_copy(
                update={
                    "version": expected_version + 1,
                    "updated_at": datetime.now(timezone.utc),
                },
                deep=True,
            )
            return await self._state_store.compare_and_set(
                user_id=context.user_id,
                task_id=context.session_id,
                state=updated,
                expected_version=expected_version,
                ttl_seconds=ttl_seconds,
            )
        except TaskStateStoreUnavailable as exc:
            raise SessionContextStoreUnavailable(
                f"Redis replace failed: {exc.__class__.__name__}"
            ) from exc

    async def refresh_ttl(
        self,
        *,
        user_id: str,
        session_id: str,
        ttl_seconds: int,
    ) -> bool:
        try:
            return await self._state_store.refresh_ttl(
                user_id=user_id,
                task_id=session_id,
                ttl_seconds=ttl_seconds,
            )
        except Exception as exc:
            raise SessionContextStoreUnavailable(
                f"Redis expiry update failed: {exc.__class__.__name__}"
            ) from exc

    async def delete(self, *, user_id: str, session_id: str) -> None:
        try:
            await self._state_store.delete(user_id=user_id, task_id=session_id)
        except Exception:
            return None

    async def delete_user(self, *, user_id: str) -> int:
        try:
            return await self._state_store.delete_user(user_id=user_id)
        except Exception:
            return 0

    async def ping(self) -> bool:
        try:
            return await self._state_store.ping()
        except Exception:
            return False

    async def close(self) -> None:
        await self._state_store.close()
