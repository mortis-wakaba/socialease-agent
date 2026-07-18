"""Shared typed Redis primitives for short-lived task session state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel


StateT = TypeVar("StateT", bound=BaseModel)


class TaskStateStoreUnavailable(RuntimeError):
    """Raised when a configured short-term task-state backend is unavailable."""


class TaskStateStore(Protocol[StateT]):
    """Typed contract shared by role-play, worksheet, and search sessions."""

    backend_name: str

    async def put(self, *, user_id: str, task_id: str, state: StateT, ttl_seconds: int) -> None: ...
    async def get(self, *, user_id: str, task_id: str) -> StateT | None: ...
    async def compare_and_set(
        self, *, user_id: str, task_id: str, state: StateT,
        expected_version: int, ttl_seconds: int
    ) -> bool: ...
    async def refresh_ttl(self, *, user_id: str, task_id: str, ttl_seconds: int) -> bool: ...
    async def delete(self, *, user_id: str, task_id: str) -> None: ...
    async def delete_user(self, *, user_id: str) -> int: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class DisabledTaskStateStore(Generic[StateT]):
    """Explicit degraded store used when Redis task sessions are not configured."""

    backend_name = "disabled"

    async def put(self, **kwargs: object) -> None:
        del kwargs
        raise TaskStateStoreUnavailable("Redis task sessions are not configured")

    async def get(self, **kwargs: object) -> StateT | None:
        del kwargs
        raise TaskStateStoreUnavailable("Redis task sessions are not configured")

    async def compare_and_set(self, **kwargs: object) -> bool:
        del kwargs
        raise TaskStateStoreUnavailable("Redis task sessions are not configured")

    async def refresh_ttl(self, **kwargs: object) -> bool:
        del kwargs
        return False

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


class InMemoryTaskStateStore(Generic[StateT]):
    """Deterministic typed task-state store for unit tests."""

    backend_name = "memory_test_double"

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._states: dict[tuple[str, str], StateT] = {}
        self._expires: dict[tuple[str, str], datetime] = {}
        self._lock = asyncio.Lock()

    async def put(self, *, user_id: str, task_id: str, state: StateT, ttl_seconds: int) -> None:
        async with self._lock:
            self._save(user_id, task_id, state, ttl_seconds)

    async def get(self, *, user_id: str, task_id: str) -> StateT | None:
        async with self._lock:
            key = (user_id, task_id)
            if self._expires.get(key, self._now()) <= self._now():
                self._states.pop(key, None)
                self._expires.pop(key, None)
                return None
            state = self._states.get(key)
            return state.model_copy(deep=True) if state is not None else None

    async def compare_and_set(
        self, *, user_id: str, task_id: str, state: StateT,
        expected_version: int, ttl_seconds: int
    ) -> bool:
        async with self._lock:
            key = (user_id, task_id)
            if self._expires.get(key, self._now()) <= self._now():
                self._states.pop(key, None)
                self._expires.pop(key, None)
                return False
            current = self._states.get(key)
            if current is None or getattr(current, "version", None) != expected_version:
                return False
            self._save(user_id, task_id, state, ttl_seconds)
            return True

    async def refresh_ttl(self, *, user_id: str, task_id: str, ttl_seconds: int) -> bool:
        async with self._lock:
            key = (user_id, task_id)
            if (
                key not in self._states
                or self._expires.get(key, self._now()) <= self._now()
            ):
                self._states.pop(key, None)
                self._expires.pop(key, None)
                return False
            self._expires[key] = self._now() + timedelta(seconds=ttl_seconds)
            return True

    async def delete(self, *, user_id: str, task_id: str) -> None:
        async with self._lock:
            self._states.pop((user_id, task_id), None)
            self._expires.pop((user_id, task_id), None)

    async def delete_user(self, *, user_id: str) -> int:
        async with self._lock:
            keys = [key for key in self._states if key[0] == user_id]
            for key in keys:
                self._states.pop(key, None)
                self._expires.pop(key, None)
            return len(keys)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    def _save(self, user_id: str, task_id: str, state: StateT, ttl_seconds: int) -> None:
        self._states[(user_id, task_id)] = state.model_copy(deep=True)
        self._expires[(user_id, task_id)] = self._now() + timedelta(seconds=ttl_seconds)


class RedisTaskStateStore(Generic[StateT]):
    """Redis JSON store with hashed ownership keys and optimistic version writes."""

    backend_name = "redis"

    def __init__(self, *, redis_url: str, namespace: str, model_type: type[StateT], socket_timeout_seconds: float = 0.5) -> None:
        self.redis_url = redis_url
        self.namespace = namespace
        self.model_type = model_type
        self.socket_timeout_seconds = socket_timeout_seconds
        self._client: Any | None = None

    async def put(self, *, user_id: str, task_id: str, state: StateT, ttl_seconds: int) -> None:
        try:
            await self._redis().set(self._key(user_id, task_id), state.model_dump_json(), ex=ttl_seconds)
        except Exception as exc:
            raise TaskStateStoreUnavailable(f"Redis write failed: {exc.__class__.__name__}") from exc

    async def get(self, *, user_id: str, task_id: str) -> StateT | None:
        try:
            raw = await self._redis().get(self._key(user_id, task_id))
            return self.model_type.model_validate_json(raw) if raw else None
        except Exception as exc:
            if isinstance(exc, TaskStateStoreUnavailable):
                raise
            raise TaskStateStoreUnavailable(f"Redis read failed: {exc.__class__.__name__}") from exc

    async def compare_and_set(
        self, *, user_id: str, task_id: str, state: StateT,
        expected_version: int, ttl_seconds: int
    ) -> bool:
        key = self._key(user_id, task_id)
        pipe = self._redis().pipeline(transaction=True)
        try:
            await pipe.watch(key)
            raw = await pipe.get(key)
            if raw is None:
                return False
            current = self.model_type.model_validate_json(raw)
            if getattr(current, "version", None) != expected_version:
                return False
            pipe.multi()
            pipe.set(key, state.model_dump_json(), ex=ttl_seconds)
            await pipe.execute()
            return True
        except Exception as exc:
            if exc.__class__.__name__ == "WatchError":
                return False
            raise TaskStateStoreUnavailable(f"Redis CAS failed: {exc.__class__.__name__}") from exc
        finally:
            await pipe.reset()

    async def refresh_ttl(self, *, user_id: str, task_id: str, ttl_seconds: int) -> bool:
        try:
            return bool(await self._redis().expire(self._key(user_id, task_id), ttl_seconds))
        except Exception as exc:
            raise TaskStateStoreUnavailable(f"Redis expiry failed: {exc.__class__.__name__}") from exc

    async def delete(self, *, user_id: str, task_id: str) -> None:
        try:
            await self._redis().delete(self._key(user_id, task_id))
        except Exception:
            return None

    async def delete_user(self, *, user_id: str) -> int:
        try:
            client = self._redis()
            keys = [key async for key in client.scan_iter(match=self._user_pattern(user_id))]
            return int(await client.delete(*keys)) if keys else 0
        except Exception:
            return 0

    async def ping(self) -> bool:
        try:
            return bool(await self._redis().ping())
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _redis(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise TaskStateStoreUnavailable("redis-py is required for task sessions") from exc
        self._client = Redis.from_url(
            self.redis_url, decode_responses=True,
            socket_connect_timeout=self.socket_timeout_seconds,
            socket_timeout=self.socket_timeout_seconds,
            health_check_interval=30,
        )
        return self._client

    def _key(self, user_id: str, task_id: str) -> str:
        return f"socialease:{self.namespace}:{_digest(user_id)}:{_digest(task_id)}"

    def _user_pattern(self, user_id: str) -> str:
        return f"socialease:{self.namespace}:{_digest(user_id)}:*"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
