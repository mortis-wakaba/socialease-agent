"""API tests for role-play practice sessions."""

import httpx
import pytest

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for role-play API tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_roleplay_start_creates_session(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "demo_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["session_id"]
    assert payload["session"]["user_id"] == "demo_user"
    assert payload["session"]["scenario"] == "classroom_speech"
    assert payload["session"]["difficulty"] == 2
    assert payload["session"]["retrieved_guidance"]["query"]
    assert payload["session"]["retrieved_guidance"]["no_guidance_found"] is False
    assert payload["session"]["retrieved_guidance"]["citations"]
    assert payload["session"]["messages"][0]["role"] == "agent"
    assert "Social Skills demo 知识库" in payload["opening_message"]
    assert payload["opening_message"]


@pytest.mark.anyio
async def test_roleplay_start_falls_back_without_guidance(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "demo_user",
            "scenario": "club_icebreaking",
            "difficulty": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    guidance = payload["session"]["retrieved_guidance"]
    assert guidance["no_guidance_found"] is True
    assert guidance["citations"] == []
    assert "通用、安全的练习脚手架" in payload["opening_message"]


@pytest.mark.anyio
async def test_roleplay_message_appends_turns(client: httpx.AsyncClient) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "demo_user",
            "scenario": "ask_teacher_question",
            "difficulty": 3,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    response = await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": "demo_user",
            "message": "老师您好，我想问一下这个作业的评分重点是什么？",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is False
    assert payload["safety_result"]["risk_level"] == "low"
    assert payload["session"]["messages"][-2]["role"] == "user"
    assert payload["session"]["messages"][-1]["role"] == "agent"
    assert payload["response"]


@pytest.mark.anyio
async def test_roleplay_feedback_returns_scores(client: httpx.AsyncClient) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "demo_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": "demo_user",
            "message": "我想先说我的核心观点，因为这个方案能让分工更清楚。",
        },
    )

    response = await client.post(
        "/api/roleplay/feedback",
        json={
            "session_id": session_id,
            "user_id": "demo_user",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    feedback = payload["feedback"]
    assert 1 <= feedback["clarity_score"] <= 5
    assert 1 <= feedback["naturalness_score"] <= 5
    assert 1 <= feedback["assertiveness_score"] <= 5
    assert 1 <= feedback["empathy_score"] <= 5
    assert feedback["strengths"]
    assert feedback["suggestions"]
    assert feedback["next_try_prompt"]
    assert feedback["citations"]
    assert feedback["citations"][0]["source"] == "Synthetic demo knowledge base"


@pytest.mark.anyio
async def test_roleplay_message_crisis_is_blocked(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "demo_user",
            "scenario": "group_discussion",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    response = await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": "demo_user",
            "message": "我不想活了，想伤害自己",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is True
    assert payload["safety_result"]["risk_level"] == "crisis"
    assert payload["session"]["messages"][-1]["role"] == "system"
    assert "角色扮演会先暂停" in payload["response"]
