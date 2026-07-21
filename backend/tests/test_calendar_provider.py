"""Deterministic tests for calendar proposal and Provider boundaries."""

import asyncio
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.calendar.mcp_client import InProcessCalendarMCPClient
from app.calendar.provider import CalendarEventNotFoundError, InMemoryCalendarProvider
from app.calendar.service import CalendarService
from app.calendar.tools import CalendarToolService
from app.models_calendar import CalendarEventProposal, CalendarRecurrence


def _proposal(*, hour: int = 20) -> CalendarEventProposal:
    return CalendarEventProposal(
        title="15分钟练习",
        start_time=datetime(2026, 7, 20, hour, 0, tzinfo=timezone.utc),
        duration_minutes=15,
        recurrence=CalendarRecurrence.DAILY,
        recurrence_end_date=date(2026, 7, 27),
        reminder_minutes=10,
    )


def test_calendar_proposal_requires_timezone_and_finite_recurrence() -> None:
    with pytest.raises(ValidationError, match="explicit timezone"):
        CalendarEventProposal(
            title="练习",
            start_time=datetime(2026, 7, 20, 20, 0),
            duration_minutes=15,
        )

    with pytest.raises(ValidationError, match="require recurrence_end_date"):
        CalendarEventProposal(
            title="练习",
            start_time=datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc),
            duration_minutes=15,
            recurrence=CalendarRecurrence.DAILY,
        )


def test_calendar_proposal_rejects_medical_title_and_unbounded_period() -> None:
    with pytest.raises(ValidationError, match="neutral and non-medical"):
        CalendarEventProposal.model_validate(
            _proposal().model_dump() | {"title": "社交焦虑症治疗"}
        )

    with pytest.raises(ValidationError, match="beyond 30 days"):
        CalendarEventProposal(
            title="练习",
            start_time=datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc),
            duration_minutes=15,
            recurrence=CalendarRecurrence.DAILY,
            recurrence_end_date=date(2026, 9, 1),
        )


@pytest.mark.anyio
async def test_provider_is_owner_scoped_and_idempotent() -> None:
    provider = InMemoryCalendarProvider()
    first = await provider.create_event(
        user_id="calendar-owner",
        proposal=_proposal(),
        idempotency_key="calendar-key-001",
    )
    repeated = await provider.create_event(
        user_id="calendar-owner",
        proposal=_proposal(),
        idempotency_key="calendar-key-001",
    )

    assert repeated.calendar_action_id == first.calendar_action_id
    assert repeated.idempotency_reused is True
    with pytest.raises(CalendarEventNotFoundError):
        await provider.get_event(
            user_id="different-user",
            calendar_action_id=first.calendar_action_id,
        )


@pytest.mark.anyio
async def test_provider_concurrent_retries_create_one_event() -> None:
    provider = InMemoryCalendarProvider()

    results = await asyncio.gather(
        *(
            provider.create_event(
                user_id="concurrent-calendar-owner",
                proposal=_proposal(),
                idempotency_key="one-logical-action",
            )
            for _ in range(24)
        )
    )
    events = await provider.list_owned_events(user_id="concurrent-calendar-owner")

    assert len({record.calendar_action_id for record in results}) == 1
    assert len(events) == 1
    assert sum(not record.idempotency_reused for record in results) == 1


@pytest.mark.anyio
async def test_service_verifies_create_with_read_after_write() -> None:
    provider = InMemoryCalendarProvider()
    service = CalendarService(
        InProcessCalendarMCPClient(CalendarToolService(provider))
    )

    response = await service.create_event(
        user_id="verified-owner",
        proposal=_proposal(),
        idempotency_key="verified-calendar-key",
    )

    assert response.verified is True
    assert response.tool_trace.read_after_write_verified is True
    assert response.tool_trace.transport == "inprocess_demo"
    assert response.tool_trace.tool_name == "create_practice_event"

    updated = await service.update_event(
        user_id="verified-owner",
        calendar_action_id=response.event.calendar_action_id,
        proposal=_proposal(hour=21),
    )
    assert updated.verified is True
    assert updated.event.proposal.start_time.hour == 21

    deleted = await service.delete_event(
        user_id="verified-owner",
        calendar_action_id=response.event.calendar_action_id,
    )
    assert deleted.verified is True
    assert deleted.event.status.value == "cancelled"
