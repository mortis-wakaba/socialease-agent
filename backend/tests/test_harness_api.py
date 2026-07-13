"""API tests for harness capability discovery."""

from uuid import uuid4

import httpx
import pytest

from app.auth.tokens import create_auth_token
from app.main import app
from app.workflow.default_hooks import metrics_hook

TEST_AUTH_SECRET = "harness-test-secret"


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client without relying on Starlette TestClient."""
    metrics_hook.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def enable_local_developer_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make local harness diagnostics explicit in tests."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")


@pytest.mark.anyio
async def test_get_harness_capabilities(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/harness/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["harness"] == "SocialEase Agent Harness"
    assert payload["design"] == "Model + Harness"
    assert "SafetyPermissionGate" in payload["runtime_loop"]
    assert payload["permission_actions"] == ["allow", "ask_consent", "down_shift", "block", "escalate"]
    assert "support_resources" in payload["knowledge_layers"]
    assert "llm_usage" in payload["observation"]
    assert "bounded_resource_agent_loop_steps" in payload["observation"]

    skills = {skill["name"]: skill for skill in payload["skills"]}
    assert skills["crisis_escalation_skill"]["has_manifest"] is True
    assert skills["worksheet_skill"]["has_manifest"] is True
    assert "cbt_worksheet" in skills["worksheet_skill"]["supported_intents"]


@pytest.mark.anyio
async def test_harness_capabilities_hidden_in_production_by_default(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.delenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", raising=False)

    response = await client.get("/api/harness/capabilities")

    assert response.status_code == 403
    assert response.json()["detail"] == "Developer endpoints are disabled."


@pytest.mark.anyio
async def test_harness_capabilities_rejects_ordinary_production_user(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    token = create_auth_token(
        user_id="ordinary_operator",
        secret=TEST_AUTH_SECRET,
        roles=("user",),
    )

    response = await client.get(
        "/api/harness/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "Developer access is required" in response.json()["detail"]


@pytest.mark.anyio
async def test_harness_capabilities_can_be_enabled_for_developer_review(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    token = create_auth_token(
        user_id="developer_operator",
        secret=TEST_AUTH_SECRET,
        roles=("developer",),
    )

    response = await client.get(
        "/api/harness/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


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
    assert payload["total_runs"] == 2
    assert payload["crisis_runs"] == 1
    assert payload["intent_counts"]["roleplay_practice"] == 1
    assert payload["intent_counts"]["crisis"] == 1
    assert payload["risk_counts"]["low"] == 1
    assert payload["risk_counts"]["crisis"] == 1
    assert payload["permission_counts"]["ask_consent"] == 1
    assert payload["permission_counts"]["escalate"] == 1
    assert payload["selected_agent_counts"]["crisis_escalation"] == 1
    assert payload["average_latency_ms"] >= 0
    assert payload["latency_p50_ms"] >= 0
    assert payload["latency_p95_ms"] >= 0
    assert payload["product_boundary_eval_counts"]["permission_ask_consent"] == 1
    assert payload["product_boundary_eval_counts"]["crisis_escalated"] == 1


@pytest.mark.anyio
async def test_get_harness_metrics_counts_privacy_memory_blocks(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/chat",
        json={
            "user_id": "metrics_privacy_user",
            "message": "我想做 worksheet，我的邮箱是 test@example.com，情境是小组讨论紧张",
            "context": {},
        },
    )

    response = await client.get("/api/harness/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_runs"] == 1
    assert payload["memory_write_blocked_runs"] == 1


@pytest.mark.anyio
async def test_intervention_plan_can_be_paused(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"pause_plan_user_{uuid4().hex}"
    chat_response = await client.post(
        "/api/chat",
        json={
            "user_id": user_id,
            "message": "我想模拟课堂发言",
            "context": {},
        },
    )
    plan_id = chat_response.json()["trace"]["intervention_plan_id"]

    pause_response = await client.post(
        f"/api/intervention-plans/{plan_id}/pause",
        params={"user_id": user_id},
    )
    list_response = await client.get(f"/api/users/{user_id}/intervention-plans")

    assert pause_response.status_code == 200
    assert pause_response.json()["plan"]["status"] == "paused"
    assert list_response.json()["plans"][0]["status"] == "paused"


@pytest.mark.anyio
async def test_intervention_plan_pause_rejects_cross_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"pause_plan_owner_{uuid4().hex}"
    other_id = f"pause_plan_other_{uuid4().hex}"
    chat_response = await client.post(
        "/api/chat",
        headers={"X-Demo-User-Id": owner_id},
        json={
            "user_id": "ignored",
            "message": "我想模拟课堂发言",
            "context": {},
        },
    )
    plan_id = chat_response.json()["trace"]["intervention_plan_id"]

    response = await client.post(
        f"/api/intervention-plans/{plan_id}/pause",
        params={"user_id": owner_id},
        headers={"X-Demo-User-Id": other_id},
    )

    assert response.status_code == 403
