"""API tests for owner-bound Calendar MCP consent and replay protection."""

import asyncio
from datetime import date, datetime, timezone

import httpx
import pytest

from app.main import app
from app.calendar.mcp_client import CalendarMCPError
from app.calendar.service import calendar_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as value:
        yield value


def _create_payload(user_id: str) -> dict[str, object]:
    return {
        "user_id": user_id,
        "proposal": {
            "title": "15分钟练习",
            "start_time": datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc).isoformat(),
            "duration_minutes": 15,
            "recurrence": "daily",
            "recurrence_end_date": date(2026, 7, 29).isoformat(),
            "reminder_minutes": 10,
        },
        "idempotency_key": f"calendar-api-{user_id}",
    }


@pytest.mark.anyio
async def test_calendar_create_requires_consumable_request_bound_consent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT", "true")
    user_id = "calendar-api-owner"
    payload = _create_payload(user_id)

    pending = await client.post("/api/calendar/events", json=payload)

    assert pending.status_code == 409
    detail = pending.json()["detail"]
    assert detail["harness_action"] == "create_calendar_event"
    protocol_id = detail["protocol_id"]

    approved = await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": user_id, "approved": True},
    )
    assert approved.status_code == 200

    created = await client.post(
        "/api/calendar/events",
        json=payload,
        headers={"X-SocialEase-Protocol-Id": protocol_id},
    )

    assert created.status_code == 200
    body = created.json()
    assert body["verified"] is True
    assert body["tool_trace"]["read_after_write_verified"] is True
    assert body["tool_trace"]["transport"] == "inprocess_demo"

    replay = await client.post(
        "/api/calendar/events",
        json=payload,
        headers={"X-SocialEase-Protocol-Id": protocol_id},
    )
    assert replay.status_code == 403


@pytest.mark.anyio
async def test_calendar_consent_cannot_be_reused_with_changed_proposal(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT", "true")
    user_id = "calendar-api-bound-owner"
    payload = _create_payload(user_id)
    pending = await client.post("/api/calendar/events", json=payload)
    protocol_id = pending.json()["detail"]["protocol_id"]
    await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": user_id, "approved": True},
    )
    changed = _create_payload(user_id)
    changed["proposal"]["duration_minutes"] = 30

    response = await client.post(
        "/api/calendar/events",
        json=changed,
        headers={"X-SocialEase-Protocol-Id": protocol_id},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_calendar_tool_failure_does_not_consume_approved_consent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT", "true")
    user_id = "calendar-api-retry-owner"
    payload = _create_payload(user_id)
    pending = await client.post("/api/calendar/events", json=payload)
    protocol_id = pending.json()["detail"]["protocol_id"]
    await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": user_id, "approved": True},
    )
    original_create = calendar_service.create_event

    async def fail_create(**_kwargs: object):
        raise CalendarMCPError("provider timeout containing no secret")

    monkeypatch.setattr(calendar_service, "create_event", fail_create)
    failed = await client.post(
        "/api/calendar/events",
        json=payload,
        headers={"X-SocialEase-Protocol-Id": protocol_id},
    )
    monkeypatch.setattr(calendar_service, "create_event", original_create)
    recovered = await client.post(
        "/api/calendar/events",
        json=payload,
        headers={"X-SocialEase-Protocol-Id": protocol_id},
    )

    assert failed.status_code == 503
    assert failed.json()["detail"] == "Calendar tool is unavailable"
    assert recovered.status_code == 200
    assert recovered.json()["verified"] is True


@pytest.mark.anyio
async def test_concurrent_calendar_replay_keeps_external_side_effect_idempotent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT", "true")
    user_id = "calendar-api-concurrent-owner"
    payload = _create_payload(user_id)
    pending = await client.post("/api/calendar/events", json=payload)
    protocol_id = pending.json()["detail"]["protocol_id"]
    await client.post(
        f"/api/protocols/{protocol_id}/respond",
        json={"user_id": user_id, "approved": True},
    )

    async def replay_once() -> httpx.Response:
        return await client.post(
            "/api/calendar/events",
            json=payload,
            headers={"X-SocialEase-Protocol-Id": protocol_id},
        )

    responses = await asyncio.gather(*(replay_once() for _ in range(8)))
    listed = await client.get("/api/calendar/events", params={"user_id": user_id})

    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in {200, 403} for response in responses)
    assert listed.status_code == 200
    assert len(listed.json()["events"]) == 1
