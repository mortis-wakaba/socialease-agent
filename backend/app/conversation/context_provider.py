"""Read bounded conversation state through one replaceable context provider."""

from dataclasses import dataclass
from typing import Protocol

from app.conversation.repository import ConversationRepository
from app.models_conversation import Conversation, ConversationEvent, ModuleRun
from app.models_conversation_context import ConversationCompactSummary


@dataclass(frozen=True)
class ConversationContextSnapshot:
    """Authoritative inputs needed before prompt-context allocation."""

    conversation: Conversation | None
    recent_events: list[ConversationEvent]
    compact_summary: ConversationCompactSummary | None
    module_stack: list[ModuleRun]


class ConversationContextProvider(Protocol):
    """Load one owner-scoped context snapshot and invalidate cached projections."""

    backend_name: str

    async def load(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_limit: int,
    ) -> ConversationContextSnapshot: ...

    async def invalidate(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None: ...


class DatabaseConversationContextProvider:
    """Read context inputs directly from the authoritative repository."""

    backend_name = "database"

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def load(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_limit: int,
    ) -> ConversationContextSnapshot:
        """Return one internally consistent owner-scoped read projection."""
        return ConversationContextSnapshot(
            conversation=self._repository.get_for_user(
                conversation_id,
                user_id,
            ),
            recent_events=self._repository.list_recent_events(
                conversation_id=conversation_id,
                user_id=user_id,
                limit=recent_limit,
            ),
            compact_summary=self._repository.get_compact_summary(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            module_stack=self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
        )

    async def invalidate(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Database reads have no projection cache to invalidate."""
        del conversation_id, user_id
