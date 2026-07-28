"""API tests for demo authentication and ownership boundaries."""

from uuid import uuid4

import httpx
import pytest

from app.auth.tokens import create_auth_token
from app.main import app
from app.models_exposure import ExposurePlanRequest
from app.models_roleplay import RoleplayStartRequest
from app.models_worksheet import WorksheetCreateRequest
from app.services.exposure_service import exposure_service
from app.services.roleplay_service import roleplay_service
from app.services.worksheet_service import worksheet_service
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
    monkeypatch.setenv(
        "SOCIALEASE_CONVERSATION_CONTENT_KEY",
        "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    monkeypatch.setenv(
        "SOCIALEASE_CONVERSATION_CONTENT_KEY_VERSION",
        "auth-boundary-test-v1",
    )


def demo_headers(user_id: str) -> dict[str, str]:
    """Return demo auth headers for a user."""
    return {"X-Demo-User-Id": user_id}


def bearer_headers(user_id: str) -> dict[str, str]:
    """Return production bearer auth headers for a user."""
    token = create_auth_token(user_id=user_id, secret=TEST_AUTH_SECRET)
    return {"Authorization": f"Bearer {token}"}


async def _create_conversation(
    client: httpx.AsyncClient,
    *,
    body_user_id: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": body_user_id,
            "title": "Auth boundary",
            "history_notice_version": "2026-07-01",
            "history_notice_acknowledged": True,
        },
    )


@pytest.mark.anyio
async def test_chat_uses_authenticated_user_over_body_user_id(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"auth_chat_owner_{uuid4().hex}"

    response = await _create_conversation(
        client,
        body_user_id="spoofed_body_user",
        headers=demo_headers(owner_id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == owner_id


@pytest.mark.anyio
async def test_trace_cannot_be_read_by_another_authenticated_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"trace_owner_{uuid4().hex}"
    other_id = f"trace_other_{uuid4().hex}"
    conversation = await _create_conversation(
        client,
        body_user_id="ignored_body_user",
        headers=demo_headers(owner_id),
    )
    turn = await client.post(
        f"/api/conversations/{conversation.json()['conversation_id']}/messages",
        headers=demo_headers(owner_id),
        json={
            "user_id": "ignored_body_user",
            "message": "今天小组交流后有点紧张。",
            "idempotency_key": f"auth-trace-{uuid4().hex}",
        },
    )
    run_id = turn.json()["workflow_response"]["run_id"]

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
    create_response = await worksheet_service.create_worksheet(
        WorksheetCreateRequest(
            user_id=owner_id,
            message="情境：课堂发言。情绪：紧张。强度：6。下一步：先写一个开场句。",
        )
    )
    assert create_response.worksheet is not None
    worksheet = create_response.worksheet
    worksheet_id = worksheet.worksheet_id

    owner_response = await client.get(
        f"/api/worksheet/{worksheet_id}",
        headers=demo_headers(owner_id),
    )
    other_response = await client.get(
        f"/api/worksheet/{worksheet_id}",
        headers=demo_headers(other_id),
    )

    assert worksheet.user_id == owner_id
    assert owner_response.status_code == 200
    assert other_response.status_code == 404


@pytest.mark.anyio
async def test_roleplay_session_cannot_be_restored_by_another_authenticated_user(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"roleplay_owner_{uuid4().hex}"
    other_id = f"roleplay_other_{uuid4().hex}"
    started = await roleplay_service.start_conversation_session(
        RoleplayStartRequest(
            user_id=owner_id,
            scenario_description="课堂上轮到我发言时练习清楚表达观点",
            difficulty=3,
        )
    )
    session = started.session.model_dump(mode="json")

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
    plan_response = await exposure_service.create_plan(
        ExposurePlanRequest(
            user_id=owner_id,
            target_scenario="课堂发言",
            current_anxiety_level=6,
        )
    )
    assert plan_response.plan is not None
    plan = plan_response.plan

    owner_response = await client.get(
        f"/api/exposure/{plan.plan_id}",
        headers=demo_headers(owner_id),
    )
    other_response = await client.get(
        f"/api/exposure/{plan.plan_id}",
        headers=demo_headers(other_id),
    )

    assert plan.user_id == owner_id
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
    delete_response = await client.request(
        "DELETE",
        f"/api/users/{owner_id}/memory",
        headers=demo_headers(other_id),
        json={"confirm_delete": True},
    )

    assert export_response.status_code == 403
    assert delete_response.status_code == 403


@pytest.mark.anyio
async def test_production_mode_requires_bearer_token(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)

    missing_response = await _create_conversation(
        client,
        body_user_id="body_user_should_not_work",
    )
    demo_header_response = await _create_conversation(
        client,
        body_user_id="body_user_should_not_work",
        headers=demo_headers("demo_header_should_not_work"),
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

    response = await _create_conversation(
        client,
        body_user_id="spoofed_body_user",
        headers=bearer_headers(owner_id),
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == owner_id


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
        "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
        "difficulty": 2,
    }

    response = await client.post(
        "/api/roleplay/start",
        headers=bearer_headers(owner_id),
        json=body,
    )

    assert response.status_code == 405


@pytest.mark.anyio
async def test_production_direct_exposure_plan_is_removed(
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

    assert response.status_code == 405


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
