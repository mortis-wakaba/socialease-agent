"""Typed Calendar MCP clients for demo and official stdio transports."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Protocol

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from app.calendar.tools import CalendarToolService
from app.models_calendar import CalendarEventProposal, CalendarEventRecord


class CalendarMCPError(RuntimeError):
    """Raised when an MCP transport or result violates the calendar contract."""


class CalendarMCPClient(Protocol):
    """Typed subset of MCP Calendar tools consumed by CalendarService."""

    transport_name: str

    async def create_event_verified(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> tuple[CalendarEventRecord, bool]: ...

    async def get_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord: ...

    async def update_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        proposal: CalendarEventProposal,
    ) -> CalendarEventRecord: ...

    async def delete_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord: ...

    async def list_owned_events(self, *, user_id: str) -> list[CalendarEventRecord]: ...

    async def list_tool_names(self) -> list[str]: ...


class InProcessCalendarMCPClient:
    """Deterministic gateway using the same tool contract without wire transport."""

    transport_name = "inprocess_demo"

    def __init__(self, tools: CalendarToolService) -> None:
        self.tools = tools

    async def create_event_verified(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> tuple[CalendarEventRecord, bool]:
        created = await self.tools.create_practice_event(
            user_id=user_id,
            proposal=proposal.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        fetched = await self.tools.get_practice_event(
            user_id=user_id,
            calendar_action_id=created.calendar_action_id,
        )
        return created, _same_event(created, fetched)

    async def get_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        return await self.tools.get_practice_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )

    async def update_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        proposal: CalendarEventProposal,
    ) -> CalendarEventRecord:
        return await self.tools.update_practice_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
            proposal=proposal.model_dump(mode="json"),
        )

    async def delete_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        return await self.tools.delete_practice_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )

    async def list_owned_events(self, *, user_id: str) -> list[CalendarEventRecord]:
        return await self.tools.list_owned_practice_events(user_id=user_id)

    async def list_tool_names(self) -> list[str]:
        return [
            "create_practice_event",
            "delete_practice_event",
            "get_practice_event",
            "list_owned_practice_events",
            "update_practice_event",
        ]


class StdioCalendarMCPClient:
    """Official MCP stdio client used for local protocol contracts and demos."""

    transport_name = "mcp_stdio"

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.backend_dir = Path(__file__).resolve().parents[2]

    async def create_event_verified(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> tuple[CalendarEventRecord, bool]:
        async with self._session() as session:
            created = _event_from_result(
                await self._call(
                    session,
                    "create_practice_event",
                    {
                        "user_id": user_id,
                        "proposal": proposal.model_dump(mode="json"),
                        "idempotency_key": idempotency_key,
                    },
                )
            )
            fetched = _event_from_result(
                await self._call(
                    session,
                    "get_practice_event",
                    {
                        "user_id": user_id,
                        "calendar_action_id": created.calendar_action_id,
                    },
                )
            )
        return created, _same_event(created, fetched)

    async def get_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        return _event_from_result(
            await self._single_call(
                "get_practice_event",
                {"user_id": user_id, "calendar_action_id": calendar_action_id},
            )
        )

    async def update_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        proposal: CalendarEventProposal,
    ) -> CalendarEventRecord:
        return _event_from_result(
            await self._single_call(
                "update_practice_event",
                {
                    "user_id": user_id,
                    "calendar_action_id": calendar_action_id,
                    "proposal": proposal.model_dump(mode="json"),
                },
            )
        )

    async def delete_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        return _event_from_result(
            await self._single_call(
                "delete_practice_event",
                {"user_id": user_id, "calendar_action_id": calendar_action_id},
            )
        )

    async def list_owned_events(self, *, user_id: str) -> list[CalendarEventRecord]:
        payload = await self._single_call(
            "list_owned_practice_events",
            {"user_id": user_id},
        )
        events = payload.get("events")
        if not isinstance(events, list):
            raise CalendarMCPError("Calendar MCP list result has no events array")
        return [CalendarEventRecord.model_validate(event) for event in events]

    async def list_tool_names(self) -> list[str]:
        async with self._session() as session:
            result = await session.list_tools()
        return sorted(tool.name for tool in result.tools)

    async def _single_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._session() as session:
            return await self._call(session, name, arguments)

    def _session(self) -> "_StdioSessionContext":
        return _StdioSessionContext(backend_dir=self.backend_dir)

    async def _call(
        self,
        session: ClientSession,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        result = await session.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
        )
        return _structured_result(result)


class StreamableHTTPCalendarMCPClient(StdioCalendarMCPClient):
    """Official recommended Streamable HTTP MCP client for remote Calendar servers."""

    transport_name = "mcp_streamable_http"

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.url = url
        self.http_client = http_client

    def _session(self) -> "_StreamableHTTPSessionContext":
        return _StreamableHTTPSessionContext(
            url=self.url,
            http_client=self.http_client,
        )


class _StdioSessionContext:
    """Open and initialize one official MCP stdio client session."""

    def __init__(self, *, backend_dir: Path) -> None:
        self.backend_dir = backend_dir
        self.stack = AsyncExitStack()

    async def __aenter__(self) -> ClientSession:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.calendar.mcp_server"],
            cwd=self.backend_dir,
            env={**os.environ, "PYTHONPATH": str(self.backend_dir)},
        )
        read_stream, write_stream = await self.stack.enter_async_context(
            stdio_client(parameters)
        )
        session = await self.stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.stack.aclose()


class _StreamableHTTPSessionContext:
    """Open and initialize one official MCP Streamable HTTP session."""

    def __init__(
        self,
        *,
        url: str,
        http_client: httpx.AsyncClient | None,
    ) -> None:
        self.url = url
        self.http_client = http_client
        self.stack = AsyncExitStack()

    async def __aenter__(self) -> ClientSession:
        read_stream, write_stream, _ = await self.stack.enter_async_context(
            streamable_http_client(self.url, http_client=self.http_client)
        )
        session = await self.stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        return session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.stack.aclose()


def _structured_result(result: CallToolResult) -> dict[str, Any]:
    if result.isError:
        raise CalendarMCPError("Calendar MCP tool returned an error")
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    for content in result.content:
        if isinstance(content, TextContent):
            try:
                payload = json.loads(content.text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    raise CalendarMCPError("Calendar MCP tool returned no structured object")


def _event_from_result(payload: dict[str, Any]) -> CalendarEventRecord:
    try:
        return CalendarEventRecord.model_validate(payload)
    except ValueError as exc:
        raise CalendarMCPError("Calendar MCP event result failed schema validation") from exc


def _same_event(created: CalendarEventRecord, fetched: CalendarEventRecord) -> bool:
    return (
        created.calendar_action_id == fetched.calendar_action_id
        and created.provider_event_id == fetched.provider_event_id
        and created.user_id == fetched.user_id
        and created.proposal == fetched.proposal
        and fetched.status == created.status
    )
