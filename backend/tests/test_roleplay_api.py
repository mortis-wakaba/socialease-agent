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
    assert payload["session"]["status"] == "active"
    assert payload["session"]["retrieved_guidance"]["query"]
    assert payload["session"]["retrieved_guidance"]["no_guidance_found"] is False
    assert payload["session"]["retrieved_guidance"]["citations"]
    assert payload["session"]["messages"][0]["role"] == "agent"
    assert "社交技巧知识库" in payload["opening_message"]
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
async def test_roleplay_get_session_restores_existing_session(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "restore_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    response = await client.get(
        f"/api/roleplay/{session_id}",
        params={"user_id": "restore_user"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["session_id"] == session_id
    assert payload["session"]["user_id"] == "restore_user"
    assert payload["opening_message"] == payload["session"]["messages"][0]["content"]

    wrong_user_response = await client.get(
        f"/api/roleplay/{session_id}",
        params={"user_id": "other_user"},
    )
    assert wrong_user_response.status_code == 404


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
    assert payload["llm_usage"]["used"] is False
    assert payload["llm_usage"]["fallback_used"] is False
    assert payload["llm_usage"]["error_category"] is None


@pytest.mark.anyio
async def test_roleplay_pause_persists_session_status(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "pause_roleplay_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    pause_response = await client.post(
        "/api/roleplay/pause",
        json={
            "session_id": session_id,
            "user_id": "pause_roleplay_user",
        },
    )
    restore_response = await client.get(
        f"/api/roleplay/{session_id}",
        params={"user_id": "pause_roleplay_user"},
    )
    message_response = await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": "pause_roleplay_user",
            "message": "我想继续练习。",
        },
    )

    assert pause_response.status_code == 200
    assert pause_response.json()["session"]["status"] == "paused"
    assert "已保存角色扮演暂停状态" in pause_response.json()["message"]
    assert restore_response.json()["session"]["status"] == "paused"
    assert message_response.status_code == 409


@pytest.mark.anyio
async def test_roleplay_pause_is_idempotent_for_paused_session(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "pause_idempotent_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    first_pause = await client.post(
        "/api/roleplay/pause",
        json={"session_id": session_id, "user_id": "pause_idempotent_user"},
    )
    second_pause = await client.post(
        "/api/roleplay/pause",
        json={"session_id": session_id, "user_id": "pause_idempotent_user"},
    )

    assert first_pause.status_code == 200
    assert second_pause.status_code == 200
    assert second_pause.json()["session"]["status"] == "paused"
    assert "已经处于暂停状态" in second_pause.json()["message"]


@pytest.mark.anyio
async def test_roleplay_resume_paused_session_allows_message_and_feedback(
    client: httpx.AsyncClient,
) -> None:
    user_id = "resume_paused_user"
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    await client.post(
        "/api/roleplay/pause",
        json={"session_id": session_id, "user_id": user_id},
    )

    resume_response = await client.post(
        "/api/roleplay/resume",
        json={"session_id": session_id, "user_id": user_id},
    )
    message_response = await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "message": "我想先说一个核心观点，因为这样能让大家更容易理解。",
        },
    )
    feedback_response = await client.post(
        "/api/roleplay/feedback",
        json={"session_id": session_id, "user_id": user_id},
    )

    assert resume_response.status_code == 200
    assert resume_response.json()["session"]["status"] == "active"
    assert "已恢复角色扮演" in resume_response.json()["message"]
    assert message_response.status_code == 200
    assert message_response.json()["session"]["status"] == "active"
    assert feedback_response.status_code == 200
    assert feedback_response.json()["session"]["status"] == "completed"


@pytest.mark.anyio
async def test_roleplay_resume_active_session_is_idempotent(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "resume_active_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    response = await client.post(
        "/api/roleplay/resume",
        json={"session_id": session_id, "user_id": "resume_active_user"},
    )

    assert response.status_code == 200
    assert response.json()["session"]["status"] == "active"
    assert "已经处于可继续练习状态" in response.json()["message"]


@pytest.mark.anyio
async def test_roleplay_resume_rejects_completed_session(
    client: httpx.AsyncClient,
) -> None:
    user_id = "resume_completed_user"
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "message": "我想先说核心观点，因为这样更清楚。",
        },
    )
    await client.post(
        "/api/roleplay/feedback",
        json={"session_id": session_id, "user_id": user_id},
    )

    response = await client.post(
        "/api/roleplay/resume",
        json={"session_id": session_id, "user_id": user_id},
    )

    assert response.status_code == 409
    assert "cannot be resumed" in response.json()["detail"]


@pytest.mark.anyio
async def test_roleplay_resume_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        headers={"X-Demo-User-Id": "roleplay_resume_owner"},
        json={
            "user_id": "ignored_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    await client.post(
        "/api/roleplay/pause",
        headers={"X-Demo-User-Id": "roleplay_resume_owner"},
        json={"session_id": session_id, "user_id": "ignored_user"},
    )

    response = await client.post(
        "/api/roleplay/resume",
        headers={"X-Demo-User-Id": "roleplay_resume_other"},
        json={"session_id": session_id, "user_id": "ignored_user"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_roleplay_feedback_requires_user_message(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "feedback_no_turn_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    feedback_response = await client.post(
        "/api/roleplay/feedback",
        json={"session_id": session_id, "user_id": "feedback_no_turn_user"},
    )

    assert feedback_response.status_code == 409
    assert "at least one user practice message" in feedback_response.json()["detail"]


@pytest.mark.anyio
async def test_roleplay_feedback_rejects_paused_session(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "feedback_paused_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": "feedback_paused_user",
            "message": "我想先表达我的观点，因为这样更清楚。",
        },
    )
    await client.post(
        "/api/roleplay/pause",
        json={"session_id": session_id, "user_id": "feedback_paused_user"},
    )

    feedback_response = await client.post(
        "/api/roleplay/feedback",
        json={"session_id": session_id, "user_id": "feedback_paused_user"},
    )

    assert feedback_response.status_code == 409
    assert "session is paused" in feedback_response.json()["detail"]


@pytest.mark.anyio
async def test_roleplay_completed_session_cannot_be_paused(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "completed_pause_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": "completed_pause_user",
            "message": "我想先说核心观点，因为这个方案能让分工更清楚。",
        },
    )
    feedback_response = await client.post(
        "/api/roleplay/feedback",
        json={"session_id": session_id, "user_id": "completed_pause_user"},
    )
    pause_response = await client.post(
        "/api/roleplay/pause",
        json={"session_id": session_id, "user_id": "completed_pause_user"},
    )

    assert feedback_response.status_code == 200
    assert feedback_response.json()["session"]["status"] == "completed"
    assert pause_response.status_code == 409
    assert "cannot be paused" in pause_response.json()["detail"]


@pytest.mark.anyio
async def test_roleplay_list_returns_recent_session_status(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "roleplay_history_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    await client.post(
        "/api/roleplay/pause",
        json={
            "session_id": session_id,
            "user_id": "roleplay_history_user",
        },
    )

    response = await client.get(
        "/api/roleplay",
        params={"user_id": "roleplay_history_user", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "roleplay_history_user"
    assert payload["sessions"][0]["session_id"] == session_id
    assert payload["sessions"][0]["status"] == "paused"


@pytest.mark.anyio
async def test_roleplay_pause_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        headers={"X-Demo-User-Id": "roleplay_pause_owner"},
        json={
            "user_id": "ignored_user",
            "scenario": "classroom_speech",
            "difficulty": 2,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    response = await client.post(
        "/api/roleplay/pause",
        headers={"X-Demo-User-Id": "roleplay_pause_other"},
        json={
            "session_id": session_id,
            "user_id": "ignored_user",
        },
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_roleplay_message_persists_privacy_safe_features(
    client: httpx.AsyncClient,
) -> None:
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": "feature_user",
            "scenario": "refuse_request",
            "difficulty": 3,
        },
    )
    session_id = start_response.json()["session"]["session_id"]
    raw_message = (
        "不好意思，我理解你很着急，但我今晚不能帮你做这个。"
        "因为我需要准备明天的课堂发言，可以周五下午再一起看吗？"
        "我的邮箱是 feature@example.com，电话 13912345678。"
    )

    response = await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": "feature_user",
            "message": raw_message,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    persisted_user_message = payload["session"]["messages"][-2]
    features = persisted_user_message["features"]
    serialized_session = str(payload["session"])
    assert persisted_user_message["role"] == "user"
    assert persisted_user_message["content"] == "[raw roleplay message minimized by privacy policy]"
    assert "feature@example.com" not in serialized_session
    assert "13912345678" not in serialized_session
    assert features["char_count"] == len(raw_message)
    assert features["has_reason"] is True
    assert features["has_request"] is True
    assert features["has_boundary_statement"] is True
    assert features["has_empathy_marker"] is True
    assert features["has_specific_time_or_place"] is True
    assert features["reason_marker_count"] >= 1
    assert features["request_marker_count"] >= 1
    assert features["boundary_marker_count"] >= 1
    assert features["empathy_marker_count"] >= 1
    assert features["politeness_marker_count"] >= 1
    assert features["specificity_marker_count"] >= 1
    assert features["collaborative_marker_count"] >= 1
    assert features["sentence_count"] >= 2
    assert set(features["sensitive_detected"]) >= {"email", "phone"}

    feedback_response = await client.post(
        "/api/roleplay/feedback",
        json={"session_id": session_id, "user_id": "feature_user"},
    )
    feedback = feedback_response.json()["feedback"]
    assert feedback["clarity_score"] >= 4
    assert feedback["assertiveness_score"] >= 4
    assert feedback["rubric_breakdown"]
    assert {item["dimension"] for item in feedback["rubric_breakdown"]} == {
        "clarity",
        "naturalness",
        "assertiveness",
        "empathy",
    }
    assert "feature@example.com" not in str(feedback["rubric_breakdown"])
    assert "13912345678" not in str(feedback["rubric_breakdown"])


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
    assert feedback["rubric_breakdown"]
    assert feedback["citations"]
    assert feedback["citations"][0]["source_name"] == "Project Authored"
    assert feedback["citations"][0]["source_type"] == "project_authored"
    assert payload["session"]["status"] == "completed"


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
    assert payload["session"]["status"] == "paused"
    assert "角色扮演会先暂停" in payload["response"]
    assert payload["llm_usage"]["used"] is False
    assert payload["llm_usage"]["fallback_used"] is False
    assert payload["llm_usage"]["error_category"] is None
