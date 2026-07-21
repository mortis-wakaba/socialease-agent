"""Consent-independent calendar orchestration over typed MCP clients."""

from __future__ import annotations

import os
from time import perf_counter

from app.calendar.mcp_client import (
    CalendarMCPClient,
    InProcessCalendarMCPClient,
    StreamableHTTPCalendarMCPClient,
)
from app.calendar.provider import InMemoryCalendarProvider
from app.calendar.tools import CalendarToolService
from app.models_calendar import (
    CalendarEventProposal,
    CalendarEventRecord,
    CalendarEventResponse,
    CalendarEventStatus,
    CalendarToolTrace,
)


class CalendarVerificationError(RuntimeError):
    """Raised when an external write cannot be confirmed by a provider read."""


class CalendarService:
    """Execute MCP calendar calls and attach privacy-minimized diagnostics."""

    def __init__(self, client: CalendarMCPClient) -> None:
        self.client = client

    async def create_event(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> CalendarEventResponse:
        """Create and read back one event before reporting success."""
        started = perf_counter()
        event, verified = await self.client.create_event_verified(
            user_id=user_id,
            proposal=proposal,
            idempotency_key=idempotency_key,
        )
        if not verified:
            raise CalendarVerificationError("calendar create read-after-write mismatch")
        return CalendarEventResponse(
            event=event,
            verified=True,
            tool_trace=CalendarToolTrace(
                tool_name="create_practice_event",
                transport=self.client.transport_name,
                status="success",
                calendar_action_id=event.calendar_action_id,
                latency_ms=(perf_counter() - started) * 1000,
                idempotency_reused=event.idempotency_reused,
                read_after_write_verified=True,
            ),
        )

    async def get_event(self, *, user_id: str, calendar_action_id: str) -> CalendarEventResponse:
        """Read one event through the configured Calendar MCP client."""
        started = perf_counter()
        event = await self.client.get_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )
        return CalendarEventResponse(
            event=event,
            verified=True,
            tool_trace=CalendarToolTrace(
                tool_name="get_practice_event",
                transport=self.client.transport_name,
                status="success",
                calendar_action_id=event.calendar_action_id,
                latency_ms=(perf_counter() - started) * 1000,
                read_after_write_verified=True,
            ),
        )

    async def update_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        proposal: CalendarEventProposal,
    ) -> CalendarEventResponse:
        """Update and read back one owner-scoped event."""
        started = perf_counter()
        event = await self.client.update_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
            proposal=proposal,
        )
        fetched = await self.client.get_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )
        verified = event.proposal == fetched.proposal and event.status == fetched.status
        if not verified:
            raise CalendarVerificationError("calendar update read-after-write mismatch")
        return CalendarEventResponse(
            event=event,
            verified=True,
            tool_trace=CalendarToolTrace(
                tool_name="update_practice_event",
                transport=self.client.transport_name,
                status="success",
                calendar_action_id=event.calendar_action_id,
                latency_ms=(perf_counter() - started) * 1000,
                read_after_write_verified=True,
            ),
        )

    async def delete_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventResponse:
        """Cancel and verify one owner-scoped event."""
        started = perf_counter()
        event = await self.client.delete_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )
        fetched = await self.client.get_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )
        verified = fetched.status == CalendarEventStatus.CANCELLED
        if not verified:
            raise CalendarVerificationError("calendar delete read-after-write mismatch")
        return CalendarEventResponse(
            event=event,
            verified=True,
            tool_trace=CalendarToolTrace(
                tool_name="delete_practice_event",
                transport=self.client.transport_name,
                status="success",
                calendar_action_id=event.calendar_action_id,
                latency_ms=(perf_counter() - started) * 1000,
                read_after_write_verified=True,
            ),
        )

    async def list_owned_events(self, *, user_id: str) -> list[CalendarEventRecord]:
        """Return only SocialEase-owned events for one user."""
        return await self.client.list_owned_events(user_id=user_id)


calendar_demo_provider = InMemoryCalendarProvider()


def create_calendar_client() -> CalendarMCPClient:
    """Use a configured real MCP endpoint or the explicitly labeled local demo."""
    url = os.getenv("SOCIALEASE_CALENDAR_MCP_URL", "").strip()
    if url:
        return StreamableHTTPCalendarMCPClient(url=url)
    return InProcessCalendarMCPClient(CalendarToolService(calendar_demo_provider))


calendar_mcp_client = create_calendar_client()
calendar_service = CalendarService(calendar_mcp_client)
