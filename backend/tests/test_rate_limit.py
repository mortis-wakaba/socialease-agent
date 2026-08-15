"""Tests for local pilot rate limiting."""

import json
import logging
import os
from uuid import uuid4

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
    RedisSlidingWindowRateLimiter,
    SlidingWindowRateLimiter,
    UnsupportedRateLimitBackendError,
)


@pytest.fixture(autouse=True)
def isolated_runtime_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep middleware unit tests independent from the metrics database."""

    async def no_op_record_rate_limit_hit() -> None:
        return None

    monkeypatch.setattr(
        "app.rate_limit.record_rate_limit_hit",
        no_op_record_rate_limit_hit,
    )


async def _ok_endpoint(request) -> JSONResponse:
    """Return a small successful response for middleware tests."""
    return JSONResponse({"ok": True})


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


def test_rate_limit_redis_backend_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOCIALEASE_RATE_LIMIT_BACKEND", "redis")
    monkeypatch.delenv("SOCIALEASE_REDIS_URL", raising=False)

    with pytest.raises(UnsupportedRateLimitBackendError, match="requires"):
        RateLimitMiddleware(Starlette(routes=[Route("/api/ping", _ok_endpoint)]))


class FakeRedisRateLimitClient:
    """Return scripted atomic Redis decisions."""

    def __init__(self, decisions: list[list[int]] | None = None) -> None:
        self.decisions = decisions or []
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, *args: object) -> list[int]:
        self.calls.append(args)
        if not self.decisions:
            raise ConnectionError("test outage")
        return self.decisions.pop(0)


class ExternallyOwnedRedisClient(FakeRedisRateLimitClient):
    """Record whether a caller accidentally closes an injected client."""

    def __init__(self) -> None:
        super().__init__([[1, 0]])
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_redis_rate_limiter_uses_atomic_shared_decisions() -> None:
    client = FakeRedisRateLimitClient([[1, 0], [0, 17]])
    limiter = RedisSlidingWindowRateLimiter(
        redis_url="redis://unused",
        limit_per_minute=1,
        redis_client=client,
    )

    first = await limiter.check("user-a")
    second = await limiter.check("user-a")

    assert first.allowed is True
    assert second.allowed is False
    assert second.retry_after_seconds == 17
    assert len(client.calls) == 2
    assert "user-a" not in str(client.calls)


@pytest.mark.anyio
async def test_redis_rate_limiter_does_not_close_injected_client() -> None:
    client = ExternallyOwnedRedisClient()
    limiter = RedisSlidingWindowRateLimiter(
        redis_url="redis://unused",
        limit_per_minute=1,
        redis_client=client,
    )

    await limiter.close()

    assert client.closed is False


@pytest.mark.anyio
async def test_redis_rate_limit_outage_fails_closed_with_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_RATE_LIMIT_BACKEND", "redis")
    limiter = RedisSlidingWindowRateLimiter(
        redis_url="redis://unused",
        limit_per_minute=1,
        redis_client=FakeRedisRateLimitClient(),
    )
    app = Starlette(routes=[Route("/api/ping", _ok_endpoint)])
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/ping")

    assert response.status_code == 503
    assert response.json()["error_category"] == "RATE_LIMIT_STORE_UNAVAILABLE"


@pytest.mark.redis_integration
@pytest.mark.anyio
async def test_real_redis_rate_limit_is_shared_across_instances() -> None:
    redis_url = os.getenv("SOCIALEASE_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("SOCIALEASE_TEST_REDIS_URL is required for Redis integration tests.")
    first = RedisSlidingWindowRateLimiter(
        redis_url=redis_url,
        limit_per_minute=1,
    )
    second = RedisSlidingWindowRateLimiter(
        redis_url=redis_url,
        limit_per_minute=1,
    )
    identity = f"redis-shared-{uuid4().hex}"
    try:
        assert (await first.check(identity)).allowed is True
        blocked = await second.check(identity)
        assert blocked.allowed is False
        assert blocked.retry_after_seconds > 0
    finally:
        await first.close()
        await second.close()
