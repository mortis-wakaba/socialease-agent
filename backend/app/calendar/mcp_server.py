"""Official MCP server exposing bounded Calendar Provider operations."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from app.calendar.provider import CalendarProvider, InMemoryCalendarProvider
from app.calendar.tools import CalendarToolService


def create_calendar_mcp_server(provider: CalendarProvider) -> FastMCP:
    """Create a Calendar MCP server around one vendor-specific Provider."""
    tools = CalendarToolService(provider)
    server = FastMCP(
        "SocialEase Calendar Tools",
        instructions=(
            "Owner-scoped calendar tools for user-approved, non-medical practice reminders. "
            "The calling harness must enforce consent before write operations."
        ),
        json_response=True,
        stateless_http=True,
        host=os.getenv("SOCIALEASE_CALENDAR_MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("SOCIALEASE_CALENDAR_MCP_PORT", "8010")),
    )

    @server.tool(name="create_practice_event", structured_output=True)
    async def create_practice_event(
        user_id: str,
        proposal: dict[str, object],
        idempotency_key: str,
    ) -> dict[str, object]:
        """Create one bounded practice event after application-owned consent."""
        record = await tools.create_practice_event(
            user_id=user_id,
            proposal=proposal,
            idempotency_key=idempotency_key,
        )
        return record.model_dump(mode="json")

    @server.tool(name="get_practice_event", structured_output=True)
    async def get_practice_event(
        user_id: str,
        calendar_action_id: str,
    ) -> dict[str, object]:
        """Read one SocialEase-created event for post-write verification."""
        record = await tools.get_practice_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )
        return record.model_dump(mode="json")

    @server.tool(name="update_practice_event", structured_output=True)
    async def update_practice_event(
        user_id: str,
        calendar_action_id: str,
        proposal: dict[str, object],
    ) -> dict[str, object]:
        """Update one SocialEase-created event owned by the caller."""
        record = await tools.update_practice_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
            proposal=proposal,
        )
        return record.model_dump(mode="json")

    @server.tool(name="delete_practice_event", structured_output=True)
    async def delete_practice_event(
        user_id: str,
        calendar_action_id: str,
    ) -> dict[str, object]:
        """Cancel one SocialEase-created event owned by the caller."""
        record = await tools.delete_practice_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )
        return record.model_dump(mode="json")

    @server.tool(name="list_owned_practice_events", structured_output=True)
    async def list_owned_practice_events(user_id: str) -> dict[str, object]:
        """List only SocialEase-created events belonging to one owner."""
        records = await tools.list_owned_practice_events(user_id=user_id)
        return {"events": [record.model_dump(mode="json") for record in records]}

    return server


calendar_mcp_provider = InMemoryCalendarProvider()
calendar_mcp_server = create_calendar_mcp_server(calendar_mcp_provider)


def main() -> None:
    """Run the local Calendar MCP server over the configured official transport."""
    transport = os.getenv(
        "SOCIALEASE_CALENDAR_MCP_SERVER_TRANSPORT",
        "streamable-http",
    ).strip()
    if transport not in {"stdio", "streamable-http"}:
        raise ValueError("Calendar MCP transport must be stdio or streamable-http")
    calendar_mcp_server.run(transport=transport)


if __name__ == "__main__":
    main()
