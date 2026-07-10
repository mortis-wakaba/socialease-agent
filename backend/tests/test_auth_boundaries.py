"""API tests for demo authentication and ownership boundaries."""

from uuid import uuid4

import httpx
import pytest

from app.auth.tokens import create_auth_token
from app.main import app
from app.safety.direct_actions import PROTOCOL_HEADER_NAME


TEST_AUTH_SECRET = "test-production-auth-secret"


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for auth-boundary tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def enable_local_developer_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make raw trace diagnostics explicit in local boundary tests."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")


def demo_headers(user_id: str) -> dict[str, str]:
    """Return demo auth headers for a user."""
    return {"X-Demo-User-Id": user_id}


def bearer_headers(user_id: str) -> dict[str, str]:
    """Return production bearer auth headers for a user."""
    token = create_auth_token(user_id=user_id, secret=TEST_AUTH_SECRET)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_chat_uses_authenticated_user_over_body_user_id(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"auth_chat_owner_{uuid4().hex}"

    response = await client.post(
        "/api/chat",
        headers=demo_headers(owner_id),
        json={
            "user_id": "spoofed_body_user",
            "message": "今天小组讨论前有点紧张，想先整理一下表达。",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["user_id"] == owner_id


@pytest.mark.anyio
async def test_trace_cannot_be_read_by_another_authenticated_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"trace_owner_{uuid4().hex}"
    other_id = f"trace_other_{uuid4().hex}"
    create_response = await client.post(
        "/api/chat",
        headers=demo_headers(owner_id),
        json={
            "user_id": "ignored_body_user",
            "message": "我想练习课堂发言，先看看怎么开头。",
            "context": {},
        },
    )
    run_id = create_response.json()["run_id"]

    owner_response = await client.get(
        f"/api/runs/{run_id}",
        headers=demo_headers(owner_id),
    )
    other_response = await client.get(
        f"/api/runs/{run_id}",
        headers=demo_headers(other_id),
    )

    assert owner_response.status_code == 200
    assert owner_response.json()["user_id"] == owner_id
    assert other_response.status_code == 404


@pytest.mark.anyio
async def test_worksheet_cannot_be_read_by_another_authenticated_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"worksheet_owner_{uuid4().hex}"
    other_id = f"worksheet_other_{uuid4().hex}"
    create_response = await client.post(
        "/api/worksheet/create",
        headers=demo_headers(owner_id),
        json={
            "user_id": "ignored_body_user",
            "message": "情境：课堂发言。情绪：紧张。强度：6。下一步：先写一个开场句。",
        },
    )
    worksheet = create_response.json()["worksheet"]
    worksheet_id = worksheet["worksheet_id"]

    owner_response = await client.get(
        f"/api/worksheet/{worksheet_id}",
        headers=demo_headers(owner_id),
    )
    other_response = await client.get(
        f"/api/worksheet/{worksheet_id}",
        headers=demo_headers(other_id),
    )

    assert worksheet["user_id"] == owner_id
    assert owner_response.status_code == 200
    assert other_response.status_code == 404


@pytest.mark.anyio
async def test_roleplay_session_cannot_be_restored_by_another_authenticated_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"roleplay_owner_{uuid4().hex}"
    other_id = f"roleplay_other_{uuid4().hex}"
    start_response = await client.post(
        "/api/roleplay/start",
        headers=demo_headers(owner_id),
        json={
            "user_id": "ignored_body_user",
            "scenario": "classroom_speech",
            "difficulty": 3,
        },
    )
    session = start_response.json()["session"]

    owner_response = await client.get(
        f"/api/roleplay/{session['session_id']}",
        headers=demo_headers(owner_id),
    )
    other_response = await client.get(
        f"/api/roleplay/{session['session_id']}",
        headers=demo_headers(other_id),
    )

    assert session["user_id"] == owner_id
    assert owner_response.status_code == 200
    assert other_response.status_code == 404


@pytest.mark.anyio
async def test_exposure_plan_cannot_be_read_by_another_authenticated_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"exposure_owner_{uuid4().hex}"
    other_id = f"exposure_other_{uuid4().hex}"
    plan_response = await client.post(
        "/api/exposure/plan",
        headers=demo_headers(owner_id),
        json={
            "user_id": "ignored_body_user",
            "target_scenario": "课堂发言",
            "current_anxiety_level": 6,
            "previous_attempts": [],
        },
    )
    plan = plan_response.json()["plan"]

    owner_response = await client.get(
        f"/api/exposure/{plan['plan_id']}",
        headers=demo_headers(owner_id),
    )
    other_response = await client.get(
        f"/api/exposure/{plan['plan_id']}",
        headers=demo_headers(other_id),
    )

    assert plan["user_id"] == owner_id
    assert owner_response.status_code == 200
    assert other_response.status_code == 404


@pytest.mark.anyio
async def test_memory_export_and_delete_reject_path_user_mismatch(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"memory_owner_{uuid4().hex}"
    other_id = f"memory_other_{uuid4().hex}"

    export_response = await client.get(
        f"/api/users/{owner_id}/memory/export",
        headers=demo_headers(other_id),
    )
    delete_response = await client.delete(
        f"/api/users/{owner_id}/memory",
        headers=demo_headers(other_id),
    )

    assert export_response.status_code == 403
    assert delete_response.status_code == 403


@pytest.mark.anyio
async def test_protocol_response_uses_authenticated_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"protocol_owner_{uuid4().hex}"
    other_id = f"protocol_other_{uuid4().hex}"
    initial_response = await client.post(
        "/api/chat",
        headers=demo_headers(owner_id),
        json={
            "user_id": "ignored_body_user",
            "message": "我想模拟课堂发言",
            "context": {},
        },
    )
    protocol_id = initial_response.json()["structured_data"]["protocol_id"]

    other_response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        headers=demo_headers(other_id),
        json={"user_id": owner_id, "approved": True},
    )
    owner_response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        headers=demo_headers(owner_id),
        json={"user_id": "spoofed_body_user", "approved": True},
    )

    assert other_response.status_code == 404
    assert owner_response.status_code == 200
    assert owner_response.json()["protocol"]["status"] == "approved"


@pytest.mark.anyio
async def test_production_mode_requires_bearer_token(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)

    missing_response = await client.post(
        "/api/chat",
        json={
            "user_id": "body_user_should_not_work",
            "message": "我想练习课堂发言。",
            "context": {},
        },
    )
    demo_header_response = await client.post(
        "/api/chat",
        headers=demo_headers("demo_header_should_not_work"),
        json={
            "user_id": "body_user_should_not_work",
            "message": "我想练习课堂发言。",
            "context": {},
        },
    )

    assert missing_response.status_code == 401
    assert demo_header_response.status_code == 401


@pytest.mark.anyio
async def test_production_mode_uses_token_identity_over_body_user_id(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    owner_id = f"prod_auth_owner_{uuid4().hex}"

    response = await client.post(
        "/api/chat",
        headers=bearer_headers(owner_id),
        json={
            "user_id": "spoofed_body_user",
            "message": "我想练习课堂发言。",
            "context": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["trace"]["user_id"] == owner_id


@pytest.mark.anyio
async def test_production_mode_rejects_path_user_mismatch(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    owner_id = f"prod_path_owner_{uuid4().hex}"
    other_id = f"prod_path_other_{uuid4().hex}"

    owner_response = await client.get(
        f"/api/users/{owner_id}/profile",
        headers=bearer_headers(owner_id),
    )
    other_response = await client.get(
        f"/api/users/{owner_id}/profile",
        headers=bearer_headers(other_id),
    )

    assert owner_response.status_code == 200
    assert other_response.status_code == 403


@pytest.mark.anyio
async def test_production_mode_invalid_token_is_rejected(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)

    response = await client.get(
        "/api/users/someone/profile",
        headers={"Authorization": "Bearer invalid.token"},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_production_direct_roleplay_requires_and_consumes_consent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    owner_id = f"prod_direct_roleplay_{uuid4().hex}"
    body = {
        "user_id": "spoofed_body_user",
        "scenario": "classroom_speech",
        "difficulty": 2,
    }

    consent_response = await client.post(
        "/api/roleplay/start",
        headers=bearer_headers(owner_id),
        json=body,
    )
    protocol_id = consent_response.json()["detail"]["protocol_id"]
    approve_response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        headers=bearer_headers(owner_id),
        json={"user_id": "spoofed_body_user", "approved": True},
    )
    mismatch_response = await client.post(
        "/api/roleplay/start",
        headers={**bearer_headers(owner_id), PROTOCOL_HEADER_NAME: protocol_id},
        json={**body, "difficulty": 3},
    )
    start_response = await client.post(
        "/api/roleplay/start",
        headers={**bearer_headers(owner_id), PROTOCOL_HEADER_NAME: protocol_id},
        json=body,
    )
    replay_response = await client.post(
        "/api/roleplay/start",
        headers={**bearer_headers(owner_id), PROTOCOL_HEADER_NAME: protocol_id},
        json=body,
    )

    assert consent_response.status_code == 409
    assert consent_response.json()["detail"]["action"] == "consent_required"
    assert consent_response.json()["detail"]["harness_action"] == "start_roleplay"
    assert approve_response.status_code == 200
    assert mismatch_response.status_code == 403
    assert start_response.status_code == 200
    assert start_response.json()["session"]["user_id"] == owner_id
    assert replay_response.status_code == 403


@pytest.mark.anyio
async def test_production_direct_exposure_plan_requires_consent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    owner_id = f"prod_direct_exposure_{uuid4().hex}"

    response = await client.post(
        "/api/exposure/plan",
        headers=bearer_headers(owner_id),
        json={
            "user_id": "spoofed_body_user",
            "target_scenario": "课堂发言",
            "current_anxiety_level": 6,
            "previous_attempts": [],
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["consent_required"] is True
    assert detail["harness_action"] == "create_exposure_plan"


@pytest.mark.anyio
async def test_production_memory_preferences_require_direct_consent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    owner_id = f"prod_memory_pref_{uuid4().hex}"
    body = {
        "consent_to_save_preferences": True,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 3,
                "preferred_feedback_style": "brief_actionable",
                "preferred_practice_scenarios": ["classroom_speech"],
            },
        }

    consent_response = await client.put(
        f"/api/users/{owner_id}/memory/preferences",
        headers=bearer_headers(owner_id),
        json=body,
    )
    protocol_id = consent_response.json()["detail"]["protocol_id"]
    await client.post(
        f"/api/protocols/{protocol_id}/respond",
        headers=bearer_headers(owner_id),
        json={"user_id": "spoofed_body_user", "approved": True},
    )
    update_response = await client.put(
        f"/api/users/{owner_id}/memory/preferences",
        headers={**bearer_headers(owner_id), PROTOCOL_HEADER_NAME: protocol_id},
        json=body,
    )

    assert consent_response.status_code == 409
    assert consent_response.json()["detail"]["harness_action"] == "write_memory"
    assert update_response.status_code == 200
    assert update_response.json()["user_id"] == owner_id
