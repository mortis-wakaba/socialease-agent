"""Vendor-neutral Calendar Provider contract and deterministic demo adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from app.models_calendar import (
    CalendarEventProposal,
    CalendarEventRecord,
    CalendarEventStatus,
)


class CalendarProviderError(RuntimeError):
    """Base failure raised by Calendar Provider adapters."""


class CalendarEventNotFoundError(CalendarProviderError):
    """Raised for absent events and owner-scope violations alike."""


class CalendarProvider(Protocol):
    """Minimal provider interface hidden behind Calendar MCP tools."""

    async def create_event(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> CalendarEventRecord: ...

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


class InMemoryCalendarProvider:
    """Deterministic demo Provider with owner scope and create idempotency."""

    def __init__(self) -> None:
        self._events: dict[str, CalendarEventRecord] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create_event(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> CalendarEventRecord:
        """Create once for each owner/idempotency pair."""
        async with self._lock:
            existing_id = self._idempotency.get((user_id, idempotency_key))
            if existing_id is not None:
                existing = self._owned_record(user_id, existing_id)
                return existing.model_copy(update={"idempotency_reused": True})
            now = datetime.now(timezone.utc)
            action_id = str(uuid4())
            record = CalendarEventRecord(
                calendar_action_id=action_id,
                provider_event_id=f"demo-{uuid4()}",
                user_id=user_id,
                proposal=proposal,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            self._events[action_id] = record
            self._idempotency[(user_id, idempotency_key)] = action_id
            return record

    async def get_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        """Return one event only to its owner."""
        async with self._lock:
            return self._owned_record(user_id, calendar_action_id)

    async def update_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        proposal: CalendarEventProposal,
    ) -> CalendarEventRecord:
        """Replace one owner-scoped proposal while preserving provider identity."""
        async with self._lock:
            record = self._owned_record(user_id, calendar_action_id)
            updated = record.model_copy(
                update={
                    "proposal": proposal,
                    "updated_at": datetime.now(timezone.utc),
                    "idempotency_reused": False,
                }
            )
            self._events[calendar_action_id] = updated
            return updated

    async def delete_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        """Soft-cancel one event so read-after-delete remains auditable."""
        async with self._lock:
            record = self._owned_record(user_id, calendar_action_id)
            cancelled = record.model_copy(
                update={
                    "status": CalendarEventStatus.CANCELLED,
                    "updated_at": datetime.now(timezone.utc),
                    "idempotency_reused": False,
                }
            )
            self._events[calendar_action_id] = cancelled
            return cancelled

    async def list_owned_events(self, *, user_id: str) -> list[CalendarEventRecord]:
        """List only events created for one owner."""
        async with self._lock:
            return sorted(
                (record for record in self._events.values() if record.user_id == user_id),
                key=lambda record: record.created_at,
                reverse=True,
            )

    def _owned_record(self, user_id: str, calendar_action_id: str) -> CalendarEventRecord:
        record = self._events.get(calendar_action_id)
        if record is None or record.user_id != user_id:
            raise CalendarEventNotFoundError("calendar event not found")
        return record
