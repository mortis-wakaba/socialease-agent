"""Auth-specific rate limit helpers for pilot account endpoints."""

from __future__ import annotations

import hashlib
import os

from fastapi import HTTPException, Request

from app.auth.tokens import auth_mode
from app.rate_limit import SlidingWindowRateLimiter


_email_limiter = SlidingWindowRateLimiter(limit_per_minute=0)
_ip_limiter = SlidingWindowRateLimiter(limit_per_minute=0)
_limiter_limit = 0


def check_auth_rate_limit(request: Request, *, action: str, email: str | None) -> None:
    """Raise 429 when one auth action exceeds its configured local budget."""
    limit = _auth_limit_per_minute()
    if limit <= 0:
        return
    email_limiter, ip_limiter = _current_limiters(limit)
    email_decision = email_limiter.check(
        _auth_bucket_key(request, action=action, email=email)
    )
    ip_decision = ip_limiter.check(_ip_bucket_key(request, action=action))
    if email_decision.allowed and ip_decision.allowed:
        return
    from app.observability.runtime_events import record_auth_rate_limit_hit

    decision = email_decision if not email_decision.allowed else ip_decision
    record_auth_rate_limit_hit()
    raise HTTPException(
        status_code=429,
        detail={
            "message": "Too many authentication attempts. Please retry later.",
            "retry_after_seconds": decision.retry_after_seconds,
        },
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _current_limiters(
    limit: int,
) -> tuple[SlidingWindowRateLimiter, SlidingWindowRateLimiter]:
    """Return limiters matching the latest env configuration."""
    global _email_limiter, _ip_limiter, _limiter_limit
    if _limiter_limit != limit:
        _email_limiter = SlidingWindowRateLimiter(limit_per_minute=limit)
        _ip_limiter = SlidingWindowRateLimiter(limit_per_minute=limit)
        _limiter_limit = limit
    return _email_limiter, _ip_limiter


def _auth_limit_per_minute() -> int:
    """Return the auth endpoint limit, where zero disables local limiting."""
    default = "5" if auth_mode() == "production" else "0"
    try:
        return max(0, int(os.getenv("SOCIALEASE_AUTH_RATE_LIMIT_PER_MINUTE", default)))
    except ValueError:
        return int(default)


def _auth_bucket_key(request: Request, *, action: str, email: str | None) -> str:
    """Return a non-secret bucket key for auth rate limiting."""
    client_host = request.client.host if request.client else "unknown"
    raw = f"{action}:{_normalize_email(email)}:{client_host}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ip_bucket_key(request: Request, *, action: str) -> str:
    """Return an IP-only bucket key to slow credential stuffing across emails."""
    client_host = request.client.host if request.client else "unknown"
    raw = f"{action}:ip:{client_host}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_email(email: str | None) -> str:
    """Return normalized email text for rate-limit bucketing."""
    return (email or "").strip().casefold()


def reset_auth_rate_limiters_for_tests() -> None:
    """Reset process-local auth limiters for isolated tests."""
    global _email_limiter, _ip_limiter, _limiter_limit
    _email_limiter = SlidingWindowRateLimiter(limit_per_minute=0)
    _ip_limiter = SlidingWindowRateLimiter(limit_per_minute=0)
    _limiter_limit = 0
