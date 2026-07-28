"""Tests for local pilot rate limiting."""

import json
import logging

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth.cookies import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME
from app.middleware import RequestIdMiddleware
from app.observability.request_logging import (
    PROCESS_TIME_HEADER,
    StructuredRequestLoggingMiddleware,
)
from app.rate_limit import (
    RateLimitMiddleware,
    SlidingWindowRateLimiter,
    UnsupportedRateLimitBackendError,
)


async def _ok_endpoint(request) -> JSONResponse:
    """Return a small successful response for middleware tests."""
    return JSONResponse({"ok": True})


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


def test_sliding_window_rate_limiter_blocks_after_limit() -> None:
    limiter = SlidingWindowRateLimiter(limit_per_minute=2, window_seconds=60)

    assert limiter.check("user-a", now=100).allowed is True
    assert limiter.check("user-a", now=101).allowed is True
    blocked = limiter.check("user-a", now=102)

    assert blocked.allowed is False
    assert blocked.retry_after_seconds > 0


def test_sliding_window_rate_limiter_is_per_key() -> None:
    limiter = SlidingWindowRateLimiter(limit_per_minute=1, window_seconds=60)

    assert limiter.check("user-a", now=100).allowed is True
    assert limiter.check("user-a", now=101).allowed is False
    assert limiter.check("user-b", now=101).allowed is True


@pytest.mark.anyio
async def test_rate_limit_middleware_returns_429_for_same_demo_user(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorded_hits = 0

    async def fake_record_rate_limit_hit() -> None:
        nonlocal recorded_hits
        recorded_hits += 1

    monkeypatch.setattr("app.rate_limit.record_rate_limit_hit", fake_record_rate_limit_hit)
    app = Starlette(routes=[Route("/api/ping", _ok_endpoint)])
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowRateLimiter(limit_per_minute=1, window_seconds=60),
    )
    app.add_middleware(StructuredRequestLoggingMiddleware, slow_request_ms=10_000)
    app.add_middleware(RequestIdMiddleware)
    transport = httpx.ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="socialease.request"):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            first = await client.get(
                "/api/ping",
                headers={"X-Demo-User-Id": "rate_user", "X-Request-Id": "rate-req-1"},
            )
            second = await client.get(
                "/api/ping",
                headers={"X-Demo-User-Id": "rate_user", "X-Request-Id": "rate-req-2"},
            )
            other_user = await client.get(
                "/api/ping",
                headers={"X-Demo-User-Id": "other_rate_user"},
            )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["x-request-id"] == "rate-req-2"
    assert second.headers[PROCESS_TIME_HEADER]
    assert second.headers["retry-after"]
    assert second.json()["error_category"] == "RATE_LIMIT_EXCEEDED"
    assert second.json()["request_id"] == "rate-req-2"
    assert other_user.status_code == 200
    assert recorded_hits == 1
    request_logs = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "socialease.request"
    ]
    assert any(
        log["path"] == "/api/ping"
        and log["status_code"] == 429
        and log["request_id"] == "rate-req-2"
        for log in request_logs
    )


@pytest.mark.anyio
async def test_rate_limit_gateway_backend_skips_local_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_RATE_LIMIT_BACKEND", "gateway")
    app = Starlette(routes=[Route("/api/ping", _ok_endpoint)])
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowRateLimiter(limit_per_minute=1, window_seconds=60),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.get("/api/ping", headers={"X-Demo-User-Id": "gateway_user"})
        second = await client.get("/api/ping", headers={"X-Demo-User-Id": "gateway_user"})

    assert first.status_code == 200
    assert second.status_code == 200


@pytest.mark.anyio
async def test_rate_limit_uses_access_cookie_bucket() -> None:
    app = Starlette(routes=[Route("/api/ping", _ok_endpoint)])
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowRateLimiter(limit_per_minute=1, window_seconds=60),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.get(
            "/api/ping",
            headers={"Cookie": f"{ACCESS_COOKIE_NAME}=access-token-a"},
        )
        second = await client.get(
            "/api/ping",
            headers={"Cookie": f"{ACCESS_COOKIE_NAME}=access-token-a"},
        )
        other_cookie = await client.get(
            "/api/ping",
            headers={"Cookie": f"{ACCESS_COOKIE_NAME}=access-token-b"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error_category"] == "RATE_LIMIT_EXCEEDED"
    assert other_cookie.status_code == 200


@pytest.mark.anyio
async def test_rate_limit_uses_refresh_cookie_when_access_cookie_missing() -> None:
    app = Starlette(routes=[Route("/api/ping", _ok_endpoint)])
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowRateLimiter(limit_per_minute=1, window_seconds=60),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.get(
            "/api/ping",
            headers={"Cookie": f"{REFRESH_COOKIE_NAME}=refresh-token-a"},
        )
        second = await client.get(
            "/api/ping",
            headers={"Cookie": f"{REFRESH_COOKIE_NAME}=refresh-token-a"},
        )
        other_cookie = await client.get(
            "/api/ping",
            headers={"Cookie": f"{REFRESH_COOKIE_NAME}=refresh-token-b"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert other_cookie.status_code == 200


@pytest.mark.anyio
async def test_rate_limit_falls_back_to_client_ip_without_identity() -> None:
    app = Starlette(routes=[Route("/api/ping", _ok_endpoint)])
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowRateLimiter(limit_per_minute=1, window_seconds=60),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.get("/api/ping")
        second = await client.get("/api/ping")

    assert first.status_code == 200
    assert second.status_code == 429


def test_rate_limit_redis_backend_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOCIALEASE_RATE_LIMIT_BACKEND", "redis")

    with pytest.raises(UnsupportedRateLimitBackendError, match="shared Redis limiter"):
        RateLimitMiddleware(
            Starlette(routes=[Route("/api/ping", _ok_endpoint)]),
            limiter=SlidingWindowRateLimiter(limit_per_minute=1, window_seconds=60),
        )
