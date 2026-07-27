"""API tests for unified conversations and proposal confirmation boundaries."""

from pathlib import Path

import httpx
import pytest

from app.api.conversations import conversation_service
from app.main import app
from app.models_conversation import HISTORY_NOTICE_VERSION


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> httpx.AsyncClient:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("LLM_ENABLED", "false")
    conversation_service.cache_clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as test_client:
        yield test_client
    conversation_service.cache_clear()


@pytest.mark.anyio
async def test_conversation_api_keeps_proposal_in_one_timeline(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Demo-User-Id": "owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "owner",
            "title": "统一对话",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]

    message = await client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={
            "user_id": "owner",
            "message": "我想做角色扮演，练习小组讨论",
            "idempotency_key": "api-message-001",
        },
    )
    assert message.status_code == 200
    payload = message.json()
    assert payload["pending_module_proposal"]["proposed_module"] == "roleplay"
    assert payload["active_module_stack"] == []
    assert [event["sequence_no"] for event in payload["appended_events"]] == [
        1,
        2,
    ]

    detail = await client.get(
        f"/api/conversations/{conversation_id}",
        headers=headers,
        params={"user_id": "owner"},
    )
    assert detail.status_code == 200
    assert len(detail.json()["events"]["items"]) == 2

    proposal = payload["pending_module_proposal"]
    rejected = await client.post(
        (
            f"/api/conversations/{conversation_id}/module-proposals/"
            f"{proposal['proposal_id']}/reject"
        ),
        headers=headers,
        json={
            "user_id": "owner",
            "request_hash": proposal["request_hash"],
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


@pytest.mark.anyio
async def test_conversation_api_hides_cross_owner_history(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(
        "/api/conversations",
        headers={"X-Demo-User-Id": "owner"},
        json={
            "user_id": "owner",
            "title": "Private",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]

    hidden = await client.get(
        f"/api/conversations/{conversation_id}",
        headers={"X-Demo-User-Id": "other"},
        params={"user_id": "owner"},
    )

    assert hidden.status_code == 404


@pytest.mark.anyio
async def test_conversation_api_accepts_and_manually_terminates_roleplay(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Demo-User-Id": "owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "owner",
            "title": "Roleplay",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]
    proposed = await client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={
            "user_id": "owner",
            "message": "我想做角色扮演，练习在小组讨论中开口",
            "idempotency_key": "api-roleplay-001",
        },
    )
    proposal = proposed.json()["pending_module_proposal"]
    accepted = await client.post(
        (
            f"/api/conversations/{conversation_id}/module-proposals/"
            f"{proposal['proposal_id']}/accept"
        ),
        headers=headers,
        json={
            "user_id": "owner",
            "request_hash": proposal["request_hash"],
        },
    )

    assert accepted.status_code == 200
    stack = accepted.json()["active_module_stack"]
    assert len(stack) == 1
    assert stack[0]["module_type"] == "roleplay"
    module_run_id = stack[0]["module_run_id"]

    terminated = await client.post(
        (
            f"/api/conversations/{conversation_id}/modules/"
            f"{module_run_id}/terminate"
        ),
        headers=headers,
        json={"user_id": "owner"},
    )
    assert terminated.status_code == 200
    assert terminated.json()["active_module_stack"] == []
