"""API tests for the SocialEase chat workflow."""

import json

import httpx
import pytest

from app.auth.tokens import create_auth_token
from app.main import app
from app.services.intervention_plan_service import intervention_plan_service


TEST_AUTH_SECRET = "test-production-auth-secret"


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


@pytest.fixture(autouse=True)
def enable_local_developer_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make raw trace diagnostics explicit in local API tests."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")


@pytest.mark.anyio
async def test_health_check_returns_generated_request_id(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


@pytest.mark.anyio
async def test_readiness_check_returns_operational_status(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["checks"]["database"]["ok"] is True
    assert payload["checks"]["database"]["database"] in {"sqlite", "postgres"}
    assert payload["checks"]["capabilities"]["ok"] is True
    assert payload["checks"]["migrations"]["ok"] is True
    assert payload["checks"]["metrics"]["ok"] is True
    assert "socialease.db" not in str(payload)
    assert "://" not in payload["checks"]["database"]["database"]


@pytest.mark.anyio
async def test_chat_trace_preserves_incoming_request_id(
    client: httpx.AsyncClient,
) -> None:
    request_id = "req-chat-observability-001"

    response = await client.post(
        "/api/chat",
        headers={"X-Request-Id": request_id},
        json={
            "user_id": "request_id_user",
            "message": "今天有点紧张，想先整理一下情绪",
            "context": {},
        },
    )

    assert response.status_code == 200
    assert response.headers["deprecation"] == "true"
    assert 'rel="successor-version"' in response.headers["link"]
    payload = response.json()
    assert response.headers["x-request-id"] == request_id
    assert payload["trace"]["request_id"] == request_id
    assert payload["trace"]["error_categories"] == []
    assert payload["structured_data"]["request_id"] == request_id
    assert payload["structured_data"]["error_categories"] == []


@pytest.mark.anyio
async def test_chat_stream_emits_progress_before_one_guarded_final_response(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat/stream",
        json={
            "user_id": "stream_user",
            "message": "我想模拟课堂发言，先从一句开场开始",
            "context": {},
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["deprecation"] == "true"
    assert 'rel="successor-version"' in response.headers["link"]
    body = response.text
    assert body.count("event: final\n") == 1
    assert body.index("event: progress\n") < body.index("event: final\n")
    assert body.rstrip().endswith("data: {}")
    for stage in ("safety", "routing", "skill", "output_guardrail", "trace"):
        assert f'"stage":"{stage}"' in body
    final_block = next(
        block for block in body.split("\n\n") if block.startswith("event: final\n")
    )
    final_payload = final_block.removeprefix("event: final\ndata: ")
    parsed = json.loads(final_payload)
    assert parsed["trace"]["product_safe"] is True
    assert parsed["response"]


@pytest.mark.anyio
async def test_http_error_response_includes_request_id(
    client: httpx.AsyncClient,
) -> None:
    request_id = "req-run-not-found-001"

    response = await client.get(
        "/api/runs/not_found",
        headers={
            "X-Demo-User-Id": "request_error_user",
            "X-Request-Id": request_id,
        },
    )

    assert response.status_code == 404
    assert response.headers["x-request-id"] == request_id
    payload = response.json()
    assert payload["detail"] == "Run not found"
    assert payload["request_id"] == request_id
    assert payload["error_category"] == "HTTP_ERROR"


@pytest.mark.anyio
async def test_raw_run_detail_hidden_in_production_by_default(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    monkeypatch.delenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", raising=False)
    token = create_auth_token(user_id="trace_operator", secret=TEST_AUTH_SECRET)

    response = await client.get(
        "/api/runs/any_run",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Developer endpoints are disabled."


@pytest.mark.anyio
async def test_raw_run_detail_allows_developer_in_production(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    token = create_auth_token(
        user_id="developer_trace_user",
        secret=TEST_AUTH_SECRET,
        roles=("developer",),
    )
    headers = {"Authorization": f"Bearer {token}"}
    chat_response = await client.post(
        "/api/chat",
        headers=headers,
        json={
            "user_id": "ignored_body_user",
            "message": "我想练习课堂发言，先从一句开场开始。",
            "context": {},
        },
    )
    run_id = chat_response.json()["run_id"]

    response = await client.get(f"/api/runs/{run_id}", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["user_id"] == "developer_trace_user"


@pytest.mark.anyio
async def test_chat_requires_consent_before_roleplay_skill(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "low"
    assert payload["intent"] == "roleplay_practice"
    assert payload["structured_data"]["agent"] == "lead_harness"
    assert payload["structured_data"]["action"] == "consent_required"
    assert payload["structured_data"]["consent_required"] is True
    assert payload["structured_data"]["harness_action"] == "start_roleplay"
    assert payload["structured_data"]["protocol_id"]
    assert payload["structured_data"]["protocol_status"] == "pending"
    assert payload["structured_data"]["intervention_plan_id"]
    assert payload["structured_data"]["intervention_plan"]["status"] == "pending_consent"
    assert payload["structured_data"]["intervention_plan"]["steps"][1]["requires_consent"] is True
    assert payload["structured_data"]["intervention_plan"]["steps"][1]["status"] == "in_progress"
    assert payload["trace"]["selected_agent"] == "lead_harness"
    assert payload["trace"]["selected_skill"] == "lead_harness"
    assert payload["trace"]["action"] == "consent_required"
    assert payload["trace"]["intervention_plan_id"] == payload["structured_data"]["intervention_plan_id"]
    assert payload["trace"]["permission_action"] == "ask_consent"
    assert payload["trace"]["safety_result"]["risk_level"] == "low"
    assert payload["trace"]["intent_result"]["intent"] == "roleplay_practice"
    assert payload["trace"]["latency_ms"] >= 0


@pytest.mark.anyio
async def test_intervention_plan_timeline_api_returns_traceable_view(
    client: httpx.AsyncClient,
) -> None:
    user_id = "plan_timeline_user"
    response = await client.post(
        "/api/chat",
        json={
            "user_id": user_id,
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {},
        },
    )
    payload = response.json()
    plan_id = payload["structured_data"]["intervention_plan_id"]

    plan_response = await client.get(
        f"/api/intervention-plans/{plan_id}",
        params={"user_id": user_id},
    )

    assert plan_response.status_code == 200
    plan = plan_response.json()["plan"]
    assert plan["plan_id"] == plan_id
    assert plan["status"] == "pending_consent"
    assert plan["current_step_id"]
    assert plan["completed_steps"] == 1
    assert plan["total_steps"] == 4
    assert plan["progress_ratio"] == 0.25
    assert plan["timeline"][1]["is_current"] is True
    assert plan["timeline"][1]["requires_consent"] is True
    assert plan["timeline"][1]["protocol_id"] == payload["structured_data"]["protocol_id"]

    list_response = await client.get(f"/api/users/{user_id}/intervention-plans")
    assert list_response.status_code == 200
    listed_plans = list_response.json()["plans"]
    assert any(item["plan_id"] == plan_id for item in listed_plans)


@pytest.mark.anyio
async def test_intervention_plan_timeline_api_rejects_cross_user_access(
    client: httpx.AsyncClient,
) -> None:
    owner_id = "plan_owner_user"
    other_id = "plan_other_user"
    response = await client.post(
        "/api/chat",
        headers={"X-Demo-User-Id": owner_id},
        json={
            "user_id": "ignored_body_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {},
        },
    )
    plan_id = response.json()["structured_data"]["intervention_plan_id"]

    forbidden_response = await client.get(
        f"/api/intervention-plans/{plan_id}",
        headers={"X-Demo-User-Id": other_id},
        params={"user_id": owner_id},
    )

    assert forbidden_response.status_code == 403


@pytest.mark.anyio
async def test_legacy_chat_does_not_start_roleplay_after_approved_consent_protocol(
    client: httpx.AsyncClient,
) -> None:
    initial_response = await client.post(
        "/api/chat",
        json={
            "user_id": "consent_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {},
        },
    )
    protocol_id = initial_response.json()["structured_data"]["protocol_id"]

    protocol_response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": "consent_user", "approved": True},
    )
    assert protocol_response.status_code == 200
    assert protocol_response.json()["protocol"]["status"] == "approved"
    plan_id = protocol_response.json()["protocol"]["payload"]["intervention_plan_id"]
    approved_plan = intervention_plan_service.get_by_id(
        user_id="consent_user",
        plan_id=plan_id,
    )
    assert approved_plan is not None
    assert approved_plan.status == "active"

    followup_response = await client.post(
        "/api/chat",
        json={
            "user_id": "consent_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {"protocol_id": protocol_id},
        },
    )

    assert followup_response.status_code == 200
    payload = followup_response.json()
    assert payload["structured_data"]["agent"] == "lead_harness"
    assert payload["structured_data"]["action"] == "use_unified_conversation"
    assert payload["structured_data"]["next_ui"] == "chat"
    assert payload["structured_data"]["deprecated_entrypoint"] is True
    assert payload["structured_data"]["protocol_status"] == "consumed"
    assert payload["structured_data"]["intervention_plan_id"]
    assert payload["structured_data"]["intervention_plan"]["status"] == "completed"
    assert payload["trace"]["selected_skill"] == "roleplay_skill"
    assert payload["trace"]["permission_action"] == "allow"
    assert payload["trace"]["intervention_plan_id"] == payload["structured_data"]["intervention_plan_id"]

    reuse_response = await client.post(
        "/api/chat",
        json={
            "user_id": "consent_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {"protocol_id": protocol_id},
        },
    )
    reuse_payload = reuse_response.json()
    assert reuse_payload["structured_data"]["action"] == "consent_required"
    assert reuse_payload["structured_data"]["protocol_id"] != protocol_id
    assert reuse_payload["trace"]["permission_action"] == "ask_consent"


@pytest.mark.anyio
async def test_roleplay_protocol_cannot_approve_exposure_action(
    client: httpx.AsyncClient,
) -> None:
    initial_response = await client.post(
        "/api/chat",
        json={
            "user_id": "cross_action_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {},
        },
    )
    protocol_id = initial_response.json()["structured_data"]["protocol_id"]

    protocol_response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": "cross_action_user", "approved": True},
    )
    assert protocol_response.status_code == 200

    exposure_response = await client.post(
        "/api/chat",
        json={
            "user_id": "cross_action_user",
            "message": "我想做一个由易到难的社交暴露练习计划",
            "context": {"protocol_id": protocol_id},
        },
    )

    payload = exposure_response.json()
    assert payload["intent"] == "exposure_planning"
    assert payload["structured_data"]["action"] == "consent_required"
    assert payload["structured_data"]["protocol_id"] != protocol_id


@pytest.mark.anyio
async def test_rejected_protocol_cannot_execute_action(
    client: httpx.AsyncClient,
) -> None:
    initial_response = await client.post(
        "/api/chat",
        json={
            "user_id": "reject_protocol_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {},
        },
    )
    protocol_id = initial_response.json()["structured_data"]["protocol_id"]

    protocol_response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": "reject_protocol_user", "approved": False},
    )
    assert protocol_response.status_code == 200
    assert protocol_response.json()["protocol"]["status"] == "rejected"
    plan_id = protocol_response.json()["protocol"]["payload"]["intervention_plan_id"]
    cancelled_plan = intervention_plan_service.get_by_id(
        user_id="reject_protocol_user",
        plan_id=plan_id,
    )
    assert cancelled_plan is not None
    assert cancelled_plan.status == "cancelled"
    assert any(step.status == "cancelled" for step in cancelled_plan.steps)

    followup_response = await client.post(
        "/api/chat",
        json={
            "user_id": "reject_protocol_user",
            "message": "我想模拟课堂发言，怕自己说不清楚",
            "context": {"protocol_id": protocol_id},
        },
    )

    payload = followup_response.json()
    assert payload["structured_data"]["action"] == "consent_required"
    assert payload["structured_data"]["protocol_id"] != protocol_id


@pytest.mark.anyio
async def test_protocol_response_returns_404_for_wrong_user(
    client: httpx.AsyncClient,
) -> None:
    initial_response = await client.post(
        "/api/chat",
        json={
            "user_id": "protocol_owner",
            "message": "我想模拟课堂发言",
            "context": {},
        },
    )
    protocol_id = initial_response.json()["structured_data"]["protocol_id"]

    response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": "not_owner", "approved": True},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_chat_dispatches_specialized_skills(
    client: httpx.AsyncClient,
) -> None:
    cases = [
        ("我想做一个自动想法 worksheet，情境是小组讨论前很紧张", "cbt_worksheet", "worksheet_agent", "worksheet_created"),
        ("学校心理中心和辅导员资源怎么找", "campus_resource_query", "support_resource_rag_agent", "support_resources_queried"),
    ]

    for message, expected_intent, expected_agent, expected_action in cases:
        response = await client.post(
            "/api/chat",
            json={"user_id": "demo_user", "message": message, "context": {}},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["risk_level"] == "low"
        assert payload["intent"] == expected_intent
        assert payload["structured_data"]["agent"] == expected_agent
        assert payload["structured_data"]["action"] == expected_action
        assert payload["trace"]["selected_agent"] == expected_agent
        assert payload["trace"]["action"] == expected_action


@pytest.mark.anyio
async def test_chat_requires_consent_before_exposure_plan(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我想做一个由易到难的社交暴露练习计划",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "exposure_planning"
    assert payload["structured_data"]["action"] == "consent_required"
    assert payload["structured_data"]["harness_action"] == "create_exposure_plan"
    assert payload["structured_data"]["intervention_plan_id"]
    assert payload["trace"]["permission_action"] == "ask_consent"


@pytest.mark.anyio
async def test_chat_blocks_high_risk_active_practice(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我被威胁了，但还是想模拟课堂发言",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "high"
    assert payload["intent"] == "roleplay_practice"
    assert payload["structured_data"]["action"] == "action_blocked"
    assert payload["structured_data"]["blocked"] is True
    assert payload["trace"]["selected_agent"] == "lead_harness"
    assert payload["trace"]["permission_action"] == "block"


@pytest.mark.anyio
async def test_chat_medium_risk_exposure_requires_consent_and_downshifts_after_approval(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我焦虑到很难受，想做一个由易到难的社交暴露练习计划",
            "context": {"current_anxiety_level": 8},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "medium"
    assert payload["intent"] == "exposure_planning"
    assert payload["structured_data"]["agent"] == "lead_harness"
    assert payload["structured_data"]["action"] == "consent_required"
    assert payload["structured_data"]["requires_consent"] is True
    assert payload["structured_data"]["intensity_adjustment"] == -2
    assert payload["structured_data"]["intervention_plan_id"]
    assert payload["trace"]["selected_agent"] == "lead_harness"
    assert payload["trace"]["permission_action"] == "ask_consent"

    protocol_id = payload["structured_data"]["protocol_id"]
    protocol_response = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": "demo_user", "approved": True},
    )
    assert protocol_response.status_code == 200

    followup_response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我焦虑到很难受，想做一个由易到难的社交暴露练习计划",
            "context": {"current_anxiety_level": 8, "protocol_id": protocol_id},
        },
    )
    followup_payload = followup_response.json()
    assert followup_payload["structured_data"]["agent"] == "exposure_planner"
    assert followup_payload["structured_data"]["action"] == "exposure_plan_created"
    plan_id = followup_payload["structured_data"]["plan_id"]
    assert followup_payload["structured_data"]["session_id"] == plan_id
    assert followup_payload["structured_data"]["intervention_plan"]["session_id"] == plan_id
    assert followup_payload["trace"]["session_id"] == plan_id
    assert followup_payload["structured_data"]["permission_intensity_adjusted"] is True
    assert followup_payload["structured_data"]["permission_intensity_adjustment"] == -2
    assert followup_payload["structured_data"]["preview_tasks"][0]["difficulty"] == 2
    assert followup_payload["trace"]["permission_action"] == "allow"


@pytest.mark.anyio
async def test_chat_privacy_guard_blocks_sensitive_memory_write(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "privacy_api_user",
            "message": "我想做一个自动想法 worksheet，我的邮箱是 test@example.com，情境是小组讨论很紧张",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "cbt_worksheet"
    assert payload["structured_data"]["agent"] == "worksheet_agent"
    assert payload["structured_data"]["privacy_guard_blocked"] is True
    assert payload["structured_data"]["privacy_guard_detected"] == ["email"]
    assert "intervention_plan_id" not in payload["structured_data"]
    assert payload["trace"]["intervention_plan_id"] is None
    assert "before_memory_write_blocked:privacy_guard_detected:email" in payload["trace"]["errors"]


@pytest.mark.anyio
async def test_chat_crisis_routes_to_escalation(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我不想活了，可能会伤害自己",
            "context": {},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "crisis"
    assert payload["intent"] == "crisis"
    assert payload["structured_data"]["agent"] == "crisis_escalation"
    assert payload["trace"]["selected_agent"] == "crisis_escalation"
    assert payload["trace"]["selected_skill"] == "crisis_escalation_skill"
    assert payload["trace"]["action"] == "crisis_escalation"
    assert "当地紧急服务" in payload["response"]


@pytest.mark.anyio
async def test_get_run_returns_saved_trace(client: httpx.AsyncClient) -> None:
    chat_response = await client.post(
        "/api/chat",
        json={
            "user_id": "demo_user",
            "message": "我有点害怕宿舍沟通",
            "context": {},
        },
    )
    run_id = chat_response.json()["run_id"]

    response = await client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["user_id"] == "demo_user"
