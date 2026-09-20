"""API tests for unified conversations and proposal confirmation boundaries."""

import asyncio
import json

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("migrated_database")

from app.api.conversations import conversation_service
from app.main import app
from app.models_conversation import HISTORY_NOTICE_VERSION


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch,
) -> httpx.AsyncClient:
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
    assert [
        item["proposal_id"]
        for item in detail.json()["pending_module_proposals"]
    ] == [payload["pending_module_proposal"]["proposal_id"]]

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
async def test_low_risk_general_support_streams_sentence_events(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Demo-User-Id": "stream-owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "stream-owner",
            "title": "Stream",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]

    response = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json={
            "user_id": "stream-owner",
            "message": "最近和同学相处让我有点紧张",
            "idempotency_key": "stream-message-001",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: response_chunk" in response.text
    assert "event: response_completed" in response.text
    assert '"workflow_response"' not in response.text
    assert '"context_diagnostics"' not in response.text


@pytest.mark.anyio
async def test_crisis_persistence_failure_survives_real_sse_and_same_key_retry(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {"X-Demo-User-Id": "crisis-stream-owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "crisis-stream-owner",
            "title": "Crisis stream",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]
    service = conversation_service()
    original_append = service._repository.append_event
    failed = False

    async def fail_first_crisis_event(**kwargs: object):
        nonlocal failed
        if kwargs["event_type"].value == "crisis_escalated" and not failed:
            failed = True
            raise RuntimeError("controlled crisis event outage")
        return await original_append(**kwargs)

    monkeypatch.setattr(service._repository, "append_event", fail_first_crisis_event)
    request = {
        "user_id": "crisis-stream-owner",
        "message": "我现在想伤害自己",
        "idempotency_key": "crisis-stream-recovery-001",
    }
    degraded = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json=request,
    )
    degraded_completed = _completed_sse_result(degraded.text)

    assert degraded.status_code == 200
    assert "event: response_chunk" not in degraded.text
    assert "紧急服务" in degraded_completed["transient_response"]
    assert degraded_completed["appended_events"][-1]["event_type"] == "crisis_input"
    assert degraded_completed["crisis_persistence_status"] == (
        "degraded_retry_required"
    )
    recovery = await client.get(
        f"/api/conversations/{conversation_id}/commands/crisis-stream-recovery-001",
        headers=headers,
        params={"user_id": "crisis-stream-owner"},
    )
    assert recovery.json()["status"] == "recoverable"
    assert recovery.json()["can_abandon"] is False
    assert recovery.json()["safety_recovery_status"] == "recovery_pending"
    refused_abandon = await client.post(
        f"/api/conversations/{conversation_id}/commands/crisis-stream-recovery-001/abandon",
        headers=headers,
        json={"user_id": "crisis-stream-owner"},
    )
    assert refused_abandon.status_code == 409

    persisted = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json=request,
    )
    persisted_completed = _completed_sse_result(persisted.text)
    assert persisted_completed["transient_response"] is None
    assert persisted_completed["crisis_persistence_status"] == "persisted"
    assert any(
        event["event_type"] == "crisis_escalated"
        for event in persisted_completed["appended_events"]
    )
    detail = await client.get(
        f"/api/conversations/{conversation_id}",
        headers=headers,
        params={"user_id": "crisis-stream-owner"},
    )
    event_types = [item["event_type"] for item in detail.json()["events"]["items"]]
    assert event_types.count("crisis_input") == 1
    assert event_types.count("crisis_escalated") == 1


@pytest.mark.anyio
async def test_crisis_preemption_lease_loss_survives_real_sse(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {"X-Demo-User-Id": "crisis-lease-stream-owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "crisis-lease-stream-owner",
            "title": "Crisis lease stream",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]
    service = conversation_service()
    service._command_lease_seconds = 0.3

    async def blocked_preemption(**kwargs: object) -> list[object]:
        del kwargs
        await asyncio.Event().wait()
        return []

    async def lose_lease(**kwargs: object) -> bool:
        del kwargs
        return False

    monkeypatch.setattr(
        service._module_coordinator,
        "preempt_for_crisis",
        blocked_preemption,
    )
    monkeypatch.setattr(service._repository, "renew_command_lease", lose_lease)
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json={
            "user_id": "crisis-lease-stream-owner",
            "message": "我现在想伤害自己",
            "idempotency_key": "crisis-lease-stream-001",
        },
    )
    completed = _completed_sse_result(response.text)

    assert response.status_code == 200
    assert "紧急服务" in completed["transient_response"]
    assert completed["crisis_persistence_status"] == "degraded_retry_required"


@pytest.mark.anyio
@pytest.mark.parametrize("failed_read", ["get_for_user", "list_module_stack"])
async def test_busy_crisis_post_preemption_read_failure_recovers_and_replays_over_sse(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    failed_read: str,
) -> None:
    headers = {"X-Demo-User-Id": "busy-crisis-stream-owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "busy-crisis-stream-owner",
            "title": "Busy crisis stream",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]
    service = conversation_service()
    repository = service._repository
    await repository.claim_command(
        conversation_id=conversation_id,
        user_id="busy-crisis-stream-owner",
        idempotency_key="busy-existing-command",
        request_hash="b" * 64,
        lease_owner="busy-existing-worker",
        lease_seconds=60,
    )
    await repository.record_command_safety_classification(
        conversation_id=conversation_id,
        user_id="busy-crisis-stream-owner",
        idempotency_key="busy-existing-command",
        lease_owner="busy-existing-worker",
        crisis=False,
    )
    original_claim = repository.claim_command
    original_get = repository.get_for_user
    original_list_stack = repository.list_module_stack
    priority_claim_acquired = False
    get_calls = 0
    stack_calls = 0

    async def track_priority_claim(**kwargs: object):
        nonlocal priority_claim_acquired
        result = await original_claim(**kwargs)  # type: ignore[arg-type]
        if kwargs.get("preempt_active"):
            priority_claim_acquired = result.acquired
        return result

    async def fail_preparation_get(*args: object, **kwargs: object):
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            raise RuntimeError("controlled post-preemption get failure")
        return await original_get(*args, **kwargs)

    async def fail_preparation_stack(*args: object, **kwargs: object):
        nonlocal stack_calls
        stack_calls += 1
        if stack_calls == 2:
            raise RuntimeError("controlled post-preemption stack failure")
        return await original_list_stack(*args, **kwargs)

    monkeypatch.setattr(repository, "claim_command", track_priority_claim)
    monkeypatch.setattr(
        repository,
        failed_read,
        fail_preparation_get if failed_read == "get_for_user" else fail_preparation_stack,
    )
    request = {
        "user_id": "busy-crisis-stream-owner",
        "message": "我现在想伤害自己",
        "idempotency_key": f"busy-crisis-stream-{failed_read}",
    }
    first = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json=request,
    )
    first_result = _completed_sse_result(first.text)
    after_failure = await repository.get_command(
        conversation_id=conversation_id,
        user_id="busy-crisis-stream-owner",
        idempotency_key=request["idempotency_key"],
    )
    second = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json=request,
    )
    second_result = _completed_sse_result(second.text)
    third = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json=request,
    )
    third_result = _completed_sse_result(third.text)
    final = await repository.get_command(
        conversation_id=conversation_id,
        user_id="busy-crisis-stream-owner",
        idempotency_key=request["idempotency_key"],
    )
    detail = await client.get(
        f"/api/conversations/{conversation_id}",
        headers=headers,
        params={"user_id": "busy-crisis-stream-owner"},
    )
    event_types = [item["event_type"] for item in detail.json()["events"]["items"]]

    assert priority_claim_acquired is True
    assert first.status_code == 200
    assert "event: response_error" not in first.text
    assert "紧急服务" in first_result["transient_response"]
    assert first_result["appended_events"] == []
    assert first_result["crisis_persistence_status"] == "degraded_retry_required"
    assert after_failure is not None
    assert after_failure.status == "recoverable"
    assert after_failure.safety_recovery_status == "classification_pending"
    assert second_result["transient_response"] is None
    assert second_result["crisis_persistence_status"] == "persisted"
    assert third_result == second_result
    assert final is not None
    assert final.status == "completed"
    assert final.safety_recovery_status == "completed"
    assert event_types.count("crisis_input") == 1
    assert event_types.count("crisis_escalated") == 1


@pytest.mark.anyio
async def test_missing_command_is_retryable_only_after_owner_validation(
    client: httpx.AsyncClient,
) -> None:
    owner_headers = {"X-Demo-User-Id": "missing-command-owner"}
    created = await client.post(
        "/api/conversations",
        headers=owner_headers,
        json={
            "user_id": "missing-command-owner",
            "title": "Missing command recovery",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]

    missing = await client.get(
        f"/api/conversations/{conversation_id}/commands/never-created-command",
        headers=owner_headers,
        params={"user_id": "missing-command-owner"},
    )
    foreign = await client.get(
        f"/api/conversations/{conversation_id}/commands/never-created-command",
        headers={"X-Demo-User-Id": "different-owner"},
        params={"user_id": "different-owner"},
    )

    assert missing.status_code == 200
    assert missing.json() == {
        "status": "not_found",
        "result": None,
        "can_retry_same_request": True,
        "can_abandon": False,
        "safety_recovery_status": None,
    }
    assert foreign.status_code == 404


@pytest.mark.anyio
async def test_expired_command_can_be_recovered_abandoned_then_followed_by_new_message(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Demo-User-Id": "command-recovery-owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "command-recovery-owner",
            "title": "Command recovery",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]
    repository = conversation_service()._repository
    await repository.claim_command(
        conversation_id=conversation_id,
        user_id="command-recovery-owner",
        idempotency_key="refresh-recovery-command-001",
        request_hash="a" * 64,
        lease_owner="disconnected-worker",
        lease_seconds=60,
    )
    await repository.record_command_safety_classification(
        conversation_id=conversation_id,
        user_id="command-recovery-owner",
        idempotency_key="refresh-recovery-command-001",
        lease_owner="disconnected-worker",
        crisis=False,
    )
    await repository.release_command_lease(
        conversation_id=conversation_id,
        user_id="command-recovery-owner",
        idempotency_key="refresh-recovery-command-001",
        lease_owner="disconnected-worker",
    )

    recovery = await client.get(
        f"/api/conversations/{conversation_id}/commands/refresh-recovery-command-001",
        headers=headers,
        params={"user_id": "command-recovery-owner"},
    )
    assert recovery.json() == {
        "status": "recoverable",
        "result": None,
        "can_retry_same_request": True,
        "can_abandon": True,
        "safety_recovery_status": "not_required",
    }
    abandoned = await client.post(
        f"/api/conversations/{conversation_id}/commands/refresh-recovery-command-001/abandon",
        headers=headers,
        json={"user_id": "command-recovery-owner"},
    )
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"
    following = await client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        headers=headers,
        json={
            "user_id": "command-recovery-owner",
            "message": "现在继续一条新消息",
            "idempotency_key": "after-refresh-recovery-001",
        },
    )
    assert following.status_code == 200
    assert _completed_sse_result(following.text)["conversation"][
        "conversation_id"
    ] == conversation_id


def _completed_sse_result(payload: str) -> dict[str, object]:
    """Return the typed completion payload from one real route response."""
    for frame in payload.split("\n\n"):
        if frame.startswith("event: response_completed\n"):
            data = next(line[6:] for line in frame.splitlines() if line.startswith("data: "))
            return json.loads(data)["result"]
    raise AssertionError("response_completed was not emitted")

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


@pytest.mark.anyio
async def test_conversation_api_exports_and_idempotently_deletes_history(
    client: httpx.AsyncClient,
) -> None:
    headers = {"X-Demo-User-Id": "owner"}
    created = await client.post(
        "/api/conversations",
        headers=headers,
        json={
            "user_id": "owner",
            "title": "Export then delete",
            "history_notice_version": HISTORY_NOTICE_VERSION,
            "history_notice_acknowledged": True,
        },
    )
    conversation_id = created.json()["conversation_id"]
    await client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={
            "user_id": "owner",
            "message": "我想做角色扮演练习",
            "idempotency_key": "api-delete-001",
        },
    )

    exported = await client.get(
        f"/api/conversations/{conversation_id}/export",
        headers=headers,
        params={"user_id": "owner"},
    )
    assert exported.status_code == 200
    assert len(exported.json()["events"]) == 2

    delete_payload = {"user_id": "owner", "confirm_delete": True}
    deleted = await client.request(
        "DELETE",
        f"/api/conversations/{conversation_id}",
        headers=headers,
        json=delete_payload,
    )
    replay = await client.request(
        "DELETE",
        f"/api/conversations/{conversation_id}",
        headers=headers,
        json=delete_payload,
    )
    assert deleted.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["deleted_counts"] == deleted.json()["deleted_counts"]

    hidden = await client.get(
        f"/api/conversations/{conversation_id}",
        headers=headers,
        params={"user_id": "owner"},
    )
    assert hidden.status_code == 404
