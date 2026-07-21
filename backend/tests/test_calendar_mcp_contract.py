"""Contract test proving Calendar tools work through official MCP stdio transport."""

from datetime import date, datetime, timezone

import anyio
import httpx
import pytest

from app.calendar.mcp_client import StreamableHTTPCalendarMCPClient
from app.calendar.mcp_server import create_calendar_mcp_server
from app.calendar.provider import InMemoryCalendarProvider
from app.models_calendar import CalendarEventProposal, CalendarRecurrence


@pytest.mark.anyio
async def test_streamable_http_mcp_create_and_read_back_contract() -> None:
    server = create_calendar_mcp_server(InMemoryCalendarProvider())
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    proposal = CalendarEventProposal(
        title="MCP协议练习",
        start_time=datetime(2026, 7, 21, 19, 30, tzinfo=timezone.utc),
        duration_minutes=15,
        recurrence=CalendarRecurrence.DAILY,
        recurrence_end_date=date(2026, 7, 28),
        reminder_minutes=10,
    )

    async with server.session_manager.run():
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:8000",
        ) as http_client:
            client = StreamableHTTPCalendarMCPClient(
                url="http://localhost:8000/mcp",
                timeout_seconds=10.0,
                http_client=http_client,
            )
            with anyio.fail_after(8):
                tool_names = await client.list_tool_names()
                event, verified = await client.create_event_verified(
                    user_id="mcp-contract-owner",
                    proposal=proposal,
                    idempotency_key="mcp-contract-key-001",
                )

    assert tool_names == [
        "create_practice_event",
        "delete_practice_event",
        "get_practice_event",
        "list_owned_practice_events",
        "update_practice_event",
    ]
    assert verified is True
    assert event.proposal == proposal
    assert event.provider_event_id.startswith("demo-")
