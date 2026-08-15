"""Backend-neutral rate limiting middleware for pilot hardening."""

from collections import defaultdict, deque
from collections.abc import Awaitable
from dataclasses import dataclass
import hashlib
import inspect
import os
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.cookies import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME
from app.observability.runtime_events import record_rate_limit_hit
from app.request_context import REQUEST_ID_HEADER


@dataclass(frozen=True)
class RateLimitDecision:
    """Decision returned by the per-user rate limiter."""

    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    """Backend-neutral request-budget contract."""

    def check(self, key: str) -> RateLimitDecision | Awaitable[RateLimitDecision]: ...


class SlidingWindowRateLimiter:
    """Small per-identity sliding-window limiter for one app process."""

    def __init__(self, *, limit_per_minute: int, window_seconds: float = 60.0) -> None:
        self.limit_per_minute = max(0, limit_per_minute)
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        """Return whether a request is allowed for one identity bucket."""
        if self.limit_per_minute <= 0:
            return RateLimitDecision(allowed=True)
        current = monotonic() if now is None else now
        events = self._events[key]
        cutoff = current - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.limit_per_minute:
            retry_after = max(1, int(round(self.window_seconds - (current - events[0]))))
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=retry_after,
            )
        events.append(current)
        return RateLimitDecision(allowed=True)


class UnsupportedRateLimitBackendError(RuntimeError):
    """Raised when the configured limiter backend cannot be constructed."""


class RateLimitStoreUnavailable(RuntimeError):
    """Raised when a configured shared limiter cannot make a decision."""


class RedisSlidingWindowRateLimiter:
    """Atomic multi-process sliding-window limiter backed by Redis."""

    _SCRIPT = """
local bucket = KEYS[1]
local sequence = bucket .. ':sequence'
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
if limit <= 0 then
  return {1, 0}
end
local clock = redis.call('TIME')
local now_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local cutoff = now_ms - window_ms
redis.call('ZREMRANGEBYSCORE', bucket, '-inf', cutoff)
local count = redis.call('ZCARD', bucket)
if count >= limit then
  local oldest = redis.call('ZRANGE', bucket, 0, 0, 'WITHSCORES')
  local retry_ms = window_ms
  if oldest[2] then
    retry_ms = math.max(1, window_ms - (now_ms - tonumber(oldest[2])))
  end
  redis.call('PEXPIRE', bucket, window_ms)
  return {0, math.floor((retry_ms + 999) / 1000)}
end
local suffix = redis.call('INCR', sequence)
redis.call('ZADD', bucket, now_ms, tostring(now_ms) .. '-' .. tostring(suffix))
redis.call('PEXPIRE', bucket, window_ms)
redis.call('PEXPIRE', sequence, window_ms)
return {1, 0}
"""

    def __init__(
        self,
        *,
        redis_url: str,
        limit_per_minute: int,
        window_seconds: float = 60.0,
        redis_client: Any | None = None,
    ) -> None:
        self.redis_url = redis_url
        self.limit_per_minute = max(0, limit_per_minute)
        self.window_seconds = max(1.0, window_seconds)
        self._client = redis_client
        self._owns_client = redis_client is None

    async def check(self, key: str) -> RateLimitDecision:
        """Atomically consume one shared request budget entry."""
        if self.limit_per_minute <= 0:
            return RateLimitDecision(allowed=True)
        try:
            result = await self._redis().eval(
                self._SCRIPT,
                1,
                self._key(key),
                self.limit_per_minute,
                int(self.window_seconds * 1000),
            )
        except Exception as exc:
            raise RateLimitStoreUnavailable(
                f"Redis rate-limit check failed: {exc.__class__.__name__}"
            ) from exc
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise RateLimitStoreUnavailable("Redis rate-limit result was invalid")
        try:
            return RateLimitDecision(
                allowed=bool(int(result[0])),
                retry_after_seconds=max(0, int(result[1])),
            )
        except (TypeError, ValueError) as exc:
            raise RateLimitStoreUnavailable(
                "Redis rate-limit result was invalid"
            ) from exc

    def _redis(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RateLimitStoreUnavailable(
                "redis-py is required for shared rate limiting"
            ) from exc
        self._client = Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            health_check_interval=30,
        )
        return self._client

    async def close(self) -> None:
        """Close the lazily created Redis client when explicitly owned here."""
        if (
            self._owns_client
            and self._client is not None
            and hasattr(self._client, "aclose")
        ):
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _key(identity: str) -> str:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"socialease:rate_limit:{digest}"


def rate_limit_backend() -> str:
    """Return the configured request rate-limit backend."""
    return os.getenv("SOCIALEASE_RATE_LIMIT_BACKEND", "local").strip().lower() or "local"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests that exceed the configured per-user request budget."""

    def __init__(
        self,
        app,
        *,
        limiter: RateLimiter | None = None,
        enabled_paths_prefix: str = "/api",
    ) -> None:
        super().__init__(app)
        self.backend = rate_limit_backend()
        if self.backend not in {"local", "gateway", "redis"}:
            raise UnsupportedRateLimitBackendError(
                f"Unsupported SOCIALEASE_RATE_LIMIT_BACKEND={self.backend!r}."
            )
        limit = int(os.getenv("SOCIALEASE_RATE_LIMIT_PER_MINUTE", "0"))
        if limiter is not None:
            self.limiter = limiter
        elif self.backend == "redis":
            redis_url = os.getenv("SOCIALEASE_REDIS_URL", "").strip()
            if not redis_url:
                raise UnsupportedRateLimitBackendError(
                    "SOCIALEASE_RATE_LIMIT_BACKEND=redis requires "
                    "SOCIALEASE_REDIS_URL."
                )
            self.limiter = RedisSlidingWindowRateLimiter(
                redis_url=redis_url,
                limit_per_minute=limit,
            )
        else:
            self.limiter = SlidingWindowRateLimiter(limit_per_minute=limit)
        self.enabled_paths_prefix = enabled_paths_prefix

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith(self.enabled_paths_prefix):
            return await call_next(request)
        if self.backend == "gateway":
            return await call_next(request)
        try:
            decision = self.limiter.check(_rate_limit_key(request))
            if inspect.isawaitable(decision):
                decision = await decision
        except RateLimitStoreUnavailable:
            request_id = (
                getattr(request.state, "request_id", None)
                or request.headers.get(REQUEST_ID_HEADER)
                or str(uuid4())
            )
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Shared rate limiting is temporarily unavailable",
                    "request_id": request_id,
                    "error_category": "RATE_LIMIT_STORE_UNAVAILABLE",
                },
                headers={REQUEST_ID_HEADER: request_id},
            )
        if decision.allowed:
            return await call_next(request)
        await record_rate_limit_hit()
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            REQUEST_ID_HEADER
        ) or str(uuid4())
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "request_id": request_id,
                "error_category": "RATE_LIMIT_EXCEEDED",
                "retry_after_seconds": decision.retry_after_seconds,
            },
            headers={
                REQUEST_ID_HEADER: request_id,
                "Retry-After": str(decision.retry_after_seconds),
            },
        )


def _rate_limit_key(request: Request) -> str:
    """Return a stable non-secret bucket key for the current request."""
    demo_user = request.headers.get("X-Demo-User-Id")
    if demo_user and demo_user.strip():
        return f"demo:{demo_user.strip()}"

    authorization = request.headers.get("Authorization")
    if authorization and authorization.strip():
        digest = hashlib.sha256(authorization.strip().encode("utf-8")).hexdigest()
        return f"auth:{digest}"

    access_cookie = request.cookies.get(ACCESS_COOKIE_NAME, "").strip()
    if access_cookie:
        return f"access_cookie:{_token_digest(access_cookie)}"

    refresh_cookie = request.cookies.get(REFRESH_COOKIE_NAME, "").strip()
    if refresh_cookie:
        return f"refresh_cookie:{_token_digest(refresh_cookie)}"

    client_host = request.client.host if request.client else "unknown"
    return f"anonymous:{client_host}"


def _token_digest(token: str) -> str:
    """Return a non-secret stable digest for a token-like value."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
