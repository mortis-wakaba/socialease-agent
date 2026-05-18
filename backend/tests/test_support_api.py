"""API tests for public support-resource navigation."""

import httpx
import pytest

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for support API tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_support_query_returns_external_public_resources(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/support/query",
        json={"query": "social anxiety CBT self-help public resource"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is False
    assert payload["unknown"] is False
    assert payload["safety_result"]["risk_level"] == "low"
    assert payload["citations"]
    assert all(citation["source_type"] == "external_public" for citation in payload["citations"])


@pytest.mark.anyio
async def test_support_query_unknown_stays_unknown(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/support/query",
        json={"query": "火星土壤采样 轨道力学"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is False
    assert payload["unknown"] is True
    assert payload["citations"] == []


@pytest.mark.anyio
async def test_support_query_crisis_is_blocked(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/support/query",
        json={"query": "我不想活了，想伤害自己"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is True
    assert payload["unknown"] is False
    assert payload["citations"] == []
    assert payload["safety_result"]["risk_level"] == "crisis"
    assert "当地紧急服务" in payload["answer"]
