"""Application-owned implementations behind Calendar MCP tool schemas."""

from __future__ import annotations

from app.calendar.provider import CalendarProvider
from app.models_calendar import CalendarEventProposal, CalendarEventRecord


class CalendarToolService:
    """Execute bounded calendar operations against one configured Provider."""

    def __init__(self, provider: CalendarProvider) -> None:
        self.provider = provider

    async def create_practice_event(
        self,
        *,
        user_id: str,
        proposal: dict[str, object],
        idempotency_key: str,
    ) -> CalendarEventRecord:
        """Validate and create one owner-scoped practice event."""
        return await self.provider.create_event(
            user_id=user_id,
            proposal=CalendarEventProposal.model_validate(proposal),
            idempotency_key=idempotency_key,
        )

    async def get_practice_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        """Return one SocialEase-created event to its owner."""
        return await self.provider.get_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )

    async def update_practice_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
        proposal: dict[str, object],
    ) -> CalendarEventRecord:
        """Replace one SocialEase-created event after validating its proposal."""
        return await self.provider.update_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
            proposal=CalendarEventProposal.model_validate(proposal),
        )

    async def delete_practice_event(
        self,
        *,
        user_id: str,
        calendar_action_id: str,
    ) -> CalendarEventRecord:
        """Cancel one SocialEase-created owner-scoped event."""
        return await self.provider.delete_event(
            user_id=user_id,
            calendar_action_id=calendar_action_id,
        )

    async def list_owned_practice_events(
        self,
        *,
        user_id: str,
    ) -> list[CalendarEventRecord]:
        """List only events owned by the requesting user."""
        return await self.provider.list_owned_events(user_id=user_id)

