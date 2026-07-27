"""API tests for lightweight user-profile summaries."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from app.db.engine import connect
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
    assert payload["consent_state"] == {
        "store_conversation_history": True,
        "consent_to_practice_summary": False,
        "consent_to_save_preferences": False,
        "do_not_store_raw_messages": True,
        "allow_sensitive_memory": False,
    }
    assert payload["practice_preferences"] == {
        "preferred_roleplay_difficulty": None,
        "preferred_feedback_style": None,
        "preferred_practice_scenarios": [],
    }
    assert "练习记录与跨会话个性化分开管理" in payload["privacy_notice"]
    assert "随时撤回授权" in payload["privacy_notice"]
    assert "导出或删除自己拥有的练习记录" in payload["privacy_notice"]
    assert payload["memory_export_available"] is True
    assert payload["memory_delete_available"] is True
    assert payload["deletion_endpoint_reserved"] is False
    assert payload["export_endpoint_reserved"] is False


@pytest.mark.anyio
async def test_profile_updates_after_roleplay_and_exposure(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"profile_practice_user_{uuid4().hex}"
    await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
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
    assert summary["recent_scenarios"] == [
        "[raw exposure target scenario minimized by privacy policy]",
        "课堂上轮到我发言时练习清楚表达观点",
    ]


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
    assert response.json()["consent_state"]["do_not_store_raw_messages"] is True


@pytest.mark.anyio
async def test_memory_preferences_require_explicit_consent(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_pref_user_{uuid4().hex}"
    blocked = await client.put(
        f"/api/users/{user_id}/memory/preferences",
        json={
            "consent_to_save_preferences": False,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 3,
                "preferred_feedback_style": "brief_actionable",
                "preferred_practice_scenarios": ["classroom_speech"],
            },
        },
    )
    assert blocked.status_code == 403

    response = await client.put(
        f"/api/users/{user_id}/memory/preferences",
        json={
            "consent_to_save_preferences": True,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 3,
                "preferred_feedback_style": "brief_actionable",
                "preferred_practice_scenarios": ["classroom_speech"],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["consent_state"]["consent_to_save_preferences"] is True
    assert payload["consent_state"]["do_not_store_raw_messages"] is True
    assert payload["consent_state"]["allow_sensitive_memory"] is False
    assert payload["practice_preferences"]["preferred_roleplay_difficulty"] == 3


@pytest.mark.anyio
async def test_memory_preferences_can_be_disabled(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_pref_disable_user_{uuid4().hex}"
    await client.put(
        f"/api/users/{user_id}/memory/preferences",
        json={
            "consent_to_save_preferences": True,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 4,
                "preferred_feedback_style": "brief_actionable",
                "preferred_practice_scenarios": ["classroom_speech"],
            },
        },
    )

    response = await client.delete(f"/api/users/{user_id}/memory/preferences")

    assert response.status_code == 200
    payload = response.json()
    assert payload["consent_state"]["consent_to_save_preferences"] is False
    assert payload["consent_state"]["do_not_store_raw_messages"] is True
    assert payload["consent_state"]["allow_sensitive_memory"] is False
    assert payload["practice_preferences"] == {
        "preferred_roleplay_difficulty": None,
        "preferred_feedback_style": None,
        "preferred_practice_scenarios": [],
    }


@pytest.mark.anyio
async def test_practice_summary_personalization_consent_is_reversible(
    client: httpx.AsyncClient,
) -> None:
    """Consent controls future use without deleting the underlying product record."""
    user_id = f"memory_summary_consent_{uuid4().hex}"
    await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
            "difficulty": 3,
        },
    )

    enabled = await client.put(
        f"/api/users/{user_id}/memory/consent/practice-summary",
        json={"consent_to_practice_summary": True},
    )
    disabled = await client.put(
        f"/api/users/{user_id}/memory/consent/practice-summary",
        json={"consent_to_practice_summary": False},
    )
    profile = await client.get(f"/api/users/{user_id}/profile")

    assert enabled.status_code == 200
    assert enabled.json()["consent_state"]["consent_to_practice_summary"] is True
    assert disabled.status_code == 200
    assert disabled.json()["consent_state"]["consent_to_practice_summary"] is False
    assert profile.json()["practice_summary"]["roleplay_session_count"] == 1
    assert profile.json()["consent_state"]["consent_to_practice_summary"] is False


@pytest.mark.anyio
async def test_practice_summary_consent_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"memory_summary_owner_{uuid4().hex}"
    other_id = f"memory_summary_other_{uuid4().hex}"

    response = await client.put(
        f"/api/users/{owner_id}/memory/consent/practice-summary",
        headers={"X-Demo-User-Id": other_id},
        json={"consent_to_practice_summary": True},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_memory_preferences_reject_free_text_and_do_not_persist(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_pref_free_text_{uuid4().hex}"
    raw_sensitive_text = "我想保存手机号 13912345678 和邮箱 pref@example.com"

    response = await client.put(
        f"/api/users/{user_id}/memory/preferences",
        json={
            "consent_to_save_preferences": True,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 3,
                "preferred_feedback_style": raw_sensitive_text,
                "preferred_practice_scenarios": ["地址：北京市海淀区中关村大街27号"],
            },
        },
    )
    export_response = await client.get(f"/api/users/{user_id}/memory/export")

    assert response.status_code == 422
    serialized_export = str(export_response.json())
    assert raw_sensitive_text not in serialized_export
    assert "13912345678" not in serialized_export
    assert "pref@example.com" not in serialized_export
    assert "北京市海淀区中关村大街27号" not in serialized_export
    assert export_response.json()["records"]["user_memory_settings"] == []


@pytest.mark.anyio
async def test_memory_settings_schema_evolution_payload_is_sanitized(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_schema_guard_{uuid4().hex}"
    raw_sensitive_values = [
        "手机号 13912345678",
        "pref_schema@example.com",
        "北京市海淀区中关村大街27号",
        "姓名：张三",
    ]
    historical_payload = {
        "consent_state": {
            "consent_to_practice_summary": True,
            "consent_to_save_preferences": True,
            "do_not_store_raw_messages": True,
            "allow_sensitive_memory": False,
        },
        "practice_preferences": {
            "preferred_roleplay_difficulty": 3,
            "preferred_feedback_style": " 温和、具体、可执行 ",
            "preferred_practice_scenarios": [
                "classroom_speech",
                raw_sensitive_values[2],
            ],
        },
        "onboarding_profile": {
            "primary_goal": raw_sensitive_values[3],
            "preferred_scenario": "classroom_speech",
            "current_anxiety_level": 6,
            "practice_preference": raw_sensitive_values[1],
            "boundary_acknowledged": True,
        },
        "unexpected_free_text": raw_sensitive_values[0],
    }
    with connect() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO user_memory_settings
            (user_id, payload, updated_at) VALUES (?, ?, ?)""",
            (
                user_id,
                json.dumps(historical_payload, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    profile_response = await client.get(f"/api/users/{user_id}/profile")
    export_response = await client.get(f"/api/users/{user_id}/memory/export")

    assert profile_response.status_code == 200
    profile = profile_response.json()
    assert profile["consent_state"]["consent_to_save_preferences"] is True
    assert profile["practice_preferences"] == {
        "preferred_roleplay_difficulty": 3,
        "preferred_feedback_style": "gentle_specific",
        "preferred_practice_scenarios": ["classroom_speech"],
    }
    serialized_export = str(export_response.json())
    for raw in raw_sensitive_values:
        assert raw not in serialized_export
    exported_payload = export_response.json()["records"]["user_memory_settings"][0][
        "payload"
    ]
    assert exported_payload["practice_preferences"]["preferred_feedback_style"] == (
        "gentle_specific"
    )
    assert exported_payload["onboarding_profile"]["primary_goal"] is None
    assert exported_payload["onboarding_profile"]["practice_preference"] is None


@pytest.mark.anyio
async def test_onboarding_profile_can_be_saved_exported_and_deleted(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"onboarding_user_{uuid4().hex}"
    payload = {
        "onboarding_profile": {
            "primary_goal": "clearer_classroom_expression",
            "preferred_scenario": "classroom_speech",
            "current_anxiety_level": 6,
            "practice_preference": "short_sentence_first",
            "wants_pause_reminders": True,
            "wants_auto_review": True,
            "boundary_acknowledged": True,
        }
    }

    save_response = await client.put(f"/api/users/{user_id}/onboarding", json=payload)
    get_response = await client.get(f"/api/users/{user_id}/onboarding")
    export_response = await client.get(f"/api/users/{user_id}/memory/export")

    assert save_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["onboarding_profile"]["preferred_scenario"] == "classroom_speech"
    settings_records = export_response.json()["records"]["user_memory_settings"]
    assert settings_records
    assert "classroom_speech" in str(settings_records)

    delete_response = await client.delete(f"/api/users/{user_id}/memory")
    get_after_delete = await client.get(f"/api/users/{user_id}/onboarding")

    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_counts"]["user_memory_settings"] >= 1
    assert get_after_delete.json()["onboarding_profile"]["boundary_acknowledged"] is False


@pytest.mark.anyio
async def test_onboarding_profile_reset_uses_backend_state(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"onboarding_reset_user_{uuid4().hex}"
    payload = {
        "onboarding_profile": {
            "primary_goal": "clearer_classroom_expression",
            "preferred_scenario": "classroom_speech",
            "current_anxiety_level": 6,
            "practice_preference": "short_sentence_first",
            "wants_pause_reminders": True,
            "wants_auto_review": True,
            "boundary_acknowledged": True,
        }
    }

    await client.put(f"/api/users/{user_id}/onboarding", json=payload)
    reset_response = await client.delete(f"/api/users/{user_id}/onboarding")
    get_response = await client.get(f"/api/users/{user_id}/onboarding")

    assert reset_response.status_code == 200
    assert reset_response.json()["onboarding_profile"] == {
        "primary_goal": None,
        "preferred_scenario": None,
        "current_anxiety_level": None,
        "practice_preference": None,
        "wants_pause_reminders": True,
        "wants_auto_review": True,
        "boundary_acknowledged": False,
    }
    assert get_response.json()["onboarding_profile"] == reset_response.json()[
        "onboarding_profile"
    ]


@pytest.mark.anyio
async def test_onboarding_profile_rejects_free_text_and_do_not_persist(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"onboarding_free_text_{uuid4().hex}"
    response = await client.put(
        f"/api/users/{user_id}/onboarding",
        json={
            "onboarding_profile": {
                "primary_goal": "姓名：张三，电话 13912345678",
                "preferred_scenario": "宿舍地址：上海市浦东新区世纪大道100号",
                "current_anxiety_level": 6,
                "practice_preference": "邮箱 onboarding@example.com",
                "boundary_acknowledged": True,
            }
        },
    )
    get_response = await client.get(f"/api/users/{user_id}/onboarding")
    export_response = await client.get(f"/api/users/{user_id}/memory/export")

    assert response.status_code == 422
    assert get_response.json()["onboarding_profile"] == {
        "primary_goal": None,
        "preferred_scenario": None,
        "current_anxiety_level": None,
        "practice_preference": None,
        "wants_pause_reminders": True,
        "wants_auto_review": True,
        "boundary_acknowledged": False,
    }
    serialized_export = str(export_response.json())
    for raw in [
        "张三",
        "13912345678",
        "上海市浦东新区世纪大道100号",
        "onboarding@example.com",
    ]:
        assert raw not in serialized_export
    assert export_response.json()["records"]["user_memory_settings"] == []


@pytest.mark.anyio
async def test_onboarding_profile_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"onboarding_owner_{uuid4().hex}"
    other_id = f"onboarding_other_{uuid4().hex}"

    response = await client.put(
        f"/api/users/{owner_id}/onboarding",
        headers={"X-Demo-User-Id": other_id},
        json={"onboarding_profile": {"preferred_scenario": "classroom_speech"}},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_onboarding_profile_reset_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"onboarding_reset_owner_{uuid4().hex}"
    other_id = f"onboarding_reset_other_{uuid4().hex}"

    response = await client.delete(
        f"/api/users/{owner_id}/onboarding",
        headers={"X-Demo-User-Id": other_id},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_session_review_can_be_saved_and_listed(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"session_review_user_{uuid4().hex}"

    create_response = await client.post(
        f"/api/users/{user_id}/session-reviews",
        json={
            "source": "roleplay",
            "source_id": "session_1",
            "completed": "completed",
            "anxiety_before": 7,
            "anxiety_after": 4,
            "next_step": "下次继续一个低强度练习。",
            "save_record": True,
        },
    )
    list_response = await client.get(f"/api/users/{user_id}/session-reviews")

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["saved"] is True
    assert created["review"]["source"] == "roleplay"
    assert created["review"]["source_id"] == "session_1"
    assert created["review"]["next_step_summary"] == "下次继续一个低强度练习。"
    assert list_response.status_code == 200
    reviews = list_response.json()["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["review_id"] == created["review"]["review_id"]


@pytest.mark.anyio
async def test_session_review_crisis_text_is_not_saved(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"session_review_crisis_user_{uuid4().hex}"

    create_response = await client.post(
        f"/api/users/{user_id}/session-reviews",
        json={
            "source": "exposure",
            "source_id": "plan_1",
            "completed": "pause",
            "anxiety_before": 8,
            "anxiety_after": 9,
            "next_step": "我不想活了，可能会伤害自己。",
            "save_record": True,
        },
    )
    list_response = await client.get(f"/api/users/{user_id}/session-reviews")

    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["saved"] is False
    assert payload["review"] is None
    assert "请优先联系可信任的人" in payload["message"]
    assert list_response.json()["reviews"] == []


@pytest.mark.anyio
async def test_session_review_next_step_is_redacted_before_persistence(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"session_review_privacy_user_{uuid4().hex}"
    raw_next_step = (
        "下次和姓名：张三 练习拒绝，电话 13912345678，"
        "邮箱 review_privacy@example.com。"
    )

    create_response = await client.post(
        f"/api/users/{user_id}/session-reviews",
        json={
            "source": "general",
            "completed": "completed",
            "anxiety_before": 6,
            "anxiety_after": 4,
            "next_step": raw_next_step,
            "save_record": True,
        },
    )
    list_response = await client.get(f"/api/users/{user_id}/session-reviews")
    export_response = await client.get(f"/api/users/{user_id}/memory/export")

    assert create_response.status_code == 200
    serialized_create = str(create_response.json())
    serialized_list = str(list_response.json())
    serialized_export = str(export_response.json()["records"]["session_reviews"])
    for serialized in [serialized_create, serialized_list, serialized_export]:
        assert "张三" not in serialized
        assert "13912345678" not in serialized
        assert "review_privacy@example.com" not in serialized
    assert "[redacted:person_name]" in serialized_create
    assert "[redacted:phone]" in serialized_create
    assert "[redacted:email]" in serialized_create


@pytest.mark.anyio
async def test_session_review_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"session_review_owner_{uuid4().hex}"
    other_id = f"session_review_other_{uuid4().hex}"

    create_response = await client.post(
        f"/api/users/{owner_id}/session-reviews",
        headers={"X-Demo-User-Id": other_id},
        json={
            "source": "general",
            "completed": "completed",
            "anxiety_before": 5,
            "anxiety_after": 4,
            "next_step": "继续小步练习。",
            "save_record": True,
        },
    )
    list_response = await client.get(
        f"/api/users/{owner_id}/session-reviews",
        headers={"X-Demo-User-Id": other_id},
    )

    assert create_response.status_code == 403
    assert list_response.status_code == 403


@pytest.mark.anyio
async def test_memory_preferences_disable_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"memory_pref_owner_{uuid4().hex}"
    other_id = f"memory_pref_other_{uuid4().hex}"

    response = await client.delete(
        f"/api/users/{owner_id}/memory/preferences",
        headers={"X-Demo-User-Id": other_id},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_memory_control_actions_record_runtime_metrics(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = f"memory_metric_user_{uuid4().hex}"
    recorded: list[str] = []

    monkeypatch.setattr(
        "app.api.profile.record_memory_export",
        lambda: recorded.append("export"),
    )
    monkeypatch.setattr(
        "app.api.profile.record_memory_delete",
        lambda: recorded.append("delete"),
    )
    monkeypatch.setattr(
        "app.api.profile.record_memory_preferences_saved",
        lambda: recorded.append("preferences_saved"),
    )
    monkeypatch.setattr(
        "app.api.profile.record_memory_preferences_disabled",
        lambda: recorded.append("preferences_disabled"),
    )

    preferences_response = await client.put(
        f"/api/users/{user_id}/memory/preferences",
        json={
            "consent_to_save_preferences": True,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 3,
                "preferred_feedback_style": "brief_actionable",
                "preferred_practice_scenarios": ["classroom_speech"],
            },
        },
    )
    export_response = await client.get(f"/api/users/{user_id}/memory/export")
    disable_response = await client.delete(f"/api/users/{user_id}/memory/preferences")
    delete_response = await client.delete(f"/api/users/{user_id}/memory")

    assert preferences_response.status_code == 200
    assert export_response.status_code == 200
    assert disable_response.status_code == 200
    assert delete_response.status_code == 200
    assert recorded == [
        "preferences_saved",
        "export",
        "preferences_disabled",
        "delete",
    ]


@pytest.mark.anyio
async def test_memory_export_and_delete_are_real(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"memory_delete_user_{uuid4().hex}"
    await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
            "difficulty": 4,
        },
    )
    await client.put(
        f"/api/users/{user_id}/memory/preferences",
        json={
            "consent_to_save_preferences": True,
            "practice_preferences": {
                "preferred_roleplay_difficulty": 4,
                "preferred_feedback_style": "brief_actionable",
                "preferred_practice_scenarios": ["classroom_speech"],
            },
        },
    )
    await client.post(
        f"/api/users/{user_id}/session-reviews",
        json={
            "source": "roleplay",
            "source_id": "session_export",
            "completed": "partial",
            "anxiety_before": 6,
            "anxiety_after": 5,
            "next_step": "下次降低一点难度。",
            "save_record": True,
        },
    )

    export_response = await client.get(f"/api/users/{user_id}/memory/export")

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported["user_id"] == user_id
    assert len(exported["records"]["roleplay_sessions"]) == 1
    assert len(exported["records"]["user_memory_settings"]) == 1
    assert len(exported["records"]["session_reviews"]) == 1

    delete_response = await client.delete(f"/api/users/{user_id}/memory")

    assert delete_response.status_code == 200
    deleted = delete_response.json()
    assert deleted["deleted_counts"]["roleplay_sessions"] >= 1
    assert deleted["deleted_counts"]["user_memory_settings"] >= 1
    assert deleted["deleted_counts"]["session_reviews"] >= 1
    assert deleted["profile_after_delete"]["practice_summary"]["roleplay_session_count"] == 0
    assert deleted["profile_after_delete"]["practice_preferences"] == {
        "preferred_roleplay_difficulty": None,
        "preferred_feedback_style": None,
        "preferred_practice_scenarios": [],
    }

    export_after_delete = await client.get(f"/api/users/{user_id}/memory/export")
    assert export_after_delete.status_code == 200
    records = export_after_delete.json()["records"]
    assert all(len(rows) == 0 for rows in records.values())


@pytest.mark.anyio
async def test_memory_export_and_delete_reject_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    owner_id = f"memory_owner_{uuid4().hex}"
    other_id = f"memory_other_{uuid4().hex}"
    await client.post(
        "/api/roleplay/start",
        headers={"X-Demo-User-Id": owner_id},
        json={
            "user_id": "ignored_body_user",
            "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
            "difficulty": 3,
        },
    )

    export_response = await client.get(
        f"/api/users/{owner_id}/memory/export",
        headers={"X-Demo-User-Id": other_id},
    )
    delete_response = await client.delete(
        f"/api/users/{owner_id}/memory",
        headers={"X-Demo-User-Id": other_id},
    )
    owner_export = await client.get(
        f"/api/users/{owner_id}/memory/export",
        headers={"X-Demo-User-Id": owner_id},
    )

    assert export_response.status_code == 403
    assert delete_response.status_code == 403
    assert len(owner_export.json()["records"]["roleplay_sessions"]) == 1
