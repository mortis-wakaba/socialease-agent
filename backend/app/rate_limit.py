"""In-process rate limiting middleware for pilot hardening."""

from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import os
from time import monotonic
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
    """Raised when a configured shared limiter backend is not implemented."""


def rate_limit_backend() -> str:
    """Return the configured request rate-limit backend."""
    return os.getenv("SOCIALEASE_RATE_LIMIT_BACKEND", "local").strip().lower() or "local"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests that exceed the configured per-user request budget."""

    def __init__(
        self,
        app,
        *,
        limiter: SlidingWindowRateLimiter | None = None,
        enabled_paths_prefix: str = "/api",
    ) -> None:
        super().__init__(app)
        self.backend = rate_limit_backend()
        if self.backend == "redis":
            raise UnsupportedRateLimitBackendError(
                "SOCIALEASE_RATE_LIMIT_BACKEND=redis requires a shared Redis limiter "
                "adapter; use local for one process or gateway when enforced upstream."
            )
        if self.backend not in {"local", "gateway"}:
            raise UnsupportedRateLimitBackendError(
                f"Unsupported SOCIALEASE_RATE_LIMIT_BACKEND={self.backend!r}."
            )
        self.limiter = limiter or SlidingWindowRateLimiter(
            limit_per_minute=int(os.getenv("SOCIALEASE_RATE_LIMIT_PER_MINUTE", "0"))
        )
        self.enabled_paths_prefix = enabled_paths_prefix

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith(self.enabled_paths_prefix):
            return await call_next(request)
        if self.backend == "gateway":
            return await call_next(request)
        decision = self.limiter.check(_rate_limit_key(request))
        if decision.allowed:
            return await call_next(request)
        record_rate_limit_hit()
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
