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
    await metrics_hook.reset()
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
