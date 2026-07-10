"""Load-oriented tests for multi-user demo flows."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for multi-user tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def enable_local_developer_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make raw trace diagnostics explicit in multi-user tests."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")


@pytest.mark.anyio
async def test_concurrent_chat_runs_are_owner_scoped(
    client: httpx.AsyncClient,
) -> None:
    user_ids = [f"concurrent_chat_user_{uuid4().hex}" for _ in range(6)]

    async def run_chat(user_id: str) -> dict:
        response = await client.post(
            "/api/chat",
            headers={"X-Demo-User-Id": user_id},
            json={
                "user_id": "body_user_should_be_ignored",
                "message": "今天小组讨论前有点紧张，想整理一下表达。",
                "context": {},
            },
        )
        assert response.status_code == 200
        return response.json()

    payloads = await asyncio.gather(*(run_chat(user_id) for user_id in user_ids))

    assert {payload["trace"]["user_id"] for payload in payloads} == set(user_ids)
    for payload in payloads:
        run_response = await client.get(
            f"/api/runs/{payload['run_id']}",
            headers={"X-Demo-User-Id": payload["trace"]["user_id"]},
        )
        assert run_response.status_code == 200
        assert run_response.json()["user_id"] == payload["trace"]["user_id"]
