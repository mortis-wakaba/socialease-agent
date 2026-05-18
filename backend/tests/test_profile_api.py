"""API tests for lightweight user-profile summaries."""

import httpx
import pytest
from uuid import uuid4

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for profile API tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_profile_returns_empty_demo_summary(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/users/profile_empty_user/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "profile_empty_user"
    assert payload["practice_summary"] == {
        "recent_scenarios": [],
        "roleplay_session_count": 0,
        "worksheet_count": 0,
        "exposure_attempt_count": 0,
        "latest_anxiety_level": None,
        "preferred_difficulty": None,
    }
    assert "Demo summary only" in payload["privacy_notice"]
    assert payload["deletion_endpoint_reserved"] is True


@pytest.mark.anyio
async def test_profile_updates_after_roleplay_and_exposure(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"profile_practice_user_{uuid4().hex}"
    await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario": "classroom_speech",
            "difficulty": 4,
        },
    )
    plan_response = await client.post(
        "/api/exposure/plan",
        json={
            "user_id": user_id,
            "target_scenario": "课堂发言",
            "current_anxiety_level": 7,
            "previous_attempts": [],
        },
    )
    task_id = plan_response.json()["plan"]["tasks"][0]["task_id"]
    await client.post(
        "/api/exposure/complete",
        json={
            "user_id": user_id,
            "task_id": task_id,
            "status": "completed",
            "anxiety_before": 7,
            "anxiety_after": 5,
            "reflection": "完成了一次 demo 练习。",
        },
    )

    response = await client.get(f"/api/users/{user_id}/profile")

    assert response.status_code == 200
    summary = response.json()["practice_summary"]
    assert summary["roleplay_session_count"] == 1
    assert summary["exposure_attempt_count"] == 1
    assert summary["latest_anxiety_level"] == 5
    assert summary["preferred_difficulty"] == 4
    assert summary["recent_scenarios"] == ["课堂发言", "classroom_speech"]


@pytest.mark.anyio
async def test_crisis_chat_does_not_enter_profile_summary(
    client: httpx.AsyncClient,
) -> None:
    user_id = "profile_crisis_user"
    chat_response = await client.post(
        "/api/chat",
        json={
            "user_id": user_id,
            "message": "我不想活了，可能会伤害自己",
            "context": {},
        },
    )
    assert chat_response.json()["risk_level"] == "crisis"

    response = await client.get(f"/api/users/{user_id}/profile")

    assert response.status_code == 200
    assert response.json()["practice_summary"] == {
        "recent_scenarios": [],
        "roleplay_session_count": 0,
        "worksheet_count": 0,
        "exposure_attempt_count": 0,
        "latest_anxiety_level": None,
        "preferred_difficulty": None,
    }
