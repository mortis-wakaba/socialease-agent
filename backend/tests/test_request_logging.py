"""Tests for structured request logging middleware."""

import json
import logging

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.observability.request_logging import (
    PROCESS_TIME_HEADER,
    StructuredRequestLoggingMiddleware,
)


async def _ok_endpoint(request) -> JSONResponse:
    """Return a small successful response for middleware tests."""
    return JSONResponse({"ok": True})


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.mark.anyio
async def test_structured_request_logging_records_slow_request(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slow_requests = 0

    async def fake_record_slow_request() -> None:
        nonlocal slow_requests
        slow_requests += 1

    monkeypatch.setattr(
        "app.observability.request_logging.record_slow_request",
        fake_record_slow_request,
    )
    app = Starlette(routes=[Route("/api/ping", _ok_endpoint)])
    app.add_middleware(StructuredRequestLoggingMiddleware, slow_request_ms=0)
    transport = httpx.ASGITransport(app=app)

    with caplog.at_level(logging.INFO, logger="socialease.request"):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/api/ping",
                headers={"X-Request-Id": "log-req-1"},
            )

    assert response.status_code == 200
    assert response.headers[PROCESS_TIME_HEADER]
    assert slow_requests == 1
    request_logs = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "socialease.request"
    ]
    assert request_logs
    assert request_logs[-1]["event"] == "http_request"
    assert request_logs[-1]["request_id"] == "log-req-1"
    assert request_logs[-1]["path"] == "/api/ping"
    assert request_logs[-1]["status_code"] == 200
    assert request_logs[-1]["slow"] is True
    assert "ok" not in caplog.text
