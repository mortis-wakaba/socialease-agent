"""API tests for harness capability discovery."""

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
async def test_get_harness_capabilities(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/harness/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["harness"] == "SocialEase Agent Harness"
    assert payload["design"] == "Model + Harness"
    assert "SafetyPermissionGate" in payload["runtime_loop"]
    assert payload["permission_actions"] == ["allow", "escalate"]
    assert "support_resources" in payload["knowledge_layers"]
    assert "llm_usage" in payload["observation"]

    skills = {skill["name"]: skill for skill in payload["skills"]}
    assert skills["crisis_escalation_skill"]["has_manifest"] is True
    assert skills["worksheet_skill"]["has_manifest"] is True
    assert "cbt_worksheet" in skills["worksheet_skill"]["supported_intents"]


@pytest.mark.anyio
async def test_get_harness_metrics(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/chat",
        json={
            "user_id": "metrics_user",
            "message": "我想模拟课堂发言",
            "context": {},
        },
    )
    await client.post(
        "/api/chat",
        json={
            "user_id": "metrics_user",
            "message": "我不想活了，可能会伤害自己",
            "context": {},
        },
    )

    response = await client.get("/api/harness/metrics?limit=20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["window_size"] == 20
    assert payload["total_runs"] >= 2
    assert payload["crisis_runs"] >= 1
    assert payload["intent_counts"]["roleplay_practice"] >= 1
    assert payload["intent_counts"]["crisis"] >= 1
    assert payload["selected_agent_counts"]["crisis_escalation"] >= 1
    assert payload["average_latency_ms"] >= 0
