"""API tests for the SocialEase chat workflow."""

import httpx
import pytest

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client without relying on Starlette TestClient."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_chat_returns_support_response_and_trace(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "low"
    assert payload["intent"] == "roleplay_practice"
    assert payload["structured_data"]["agent"] == "support_agent"
    assert payload["trace"]["selected_agent"] == "support_agent"
    assert payload["trace"]["safety_result"]["risk_level"] == "low"
    assert payload["trace"]["intent_result"]["intent"] == "roleplay_practice"
    assert payload["trace"]["latency_ms"] >= 0


@pytest.mark.anyio
async def test_chat_crisis_routes_to_escalation(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我不想活了，可能会伤害自己",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "crisis"
    assert payload["intent"] == "crisis"
    assert payload["structured_data"]["agent"] == "crisis_escalation"
    assert payload["trace"]["selected_agent"] == "crisis_escalation"
    assert "当地紧急服务" in payload["response"]


@pytest.mark.anyio
async def test_get_run_returns_saved_trace(client: httpx.AsyncClient) -> None:
    chat_response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我有点害怕宿舍沟通",
            "context": {},
        },
    )
    run_id = chat_response.json()["run_id"]

    response = await client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["user_id"] == "demo_user"
