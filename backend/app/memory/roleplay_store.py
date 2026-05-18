"""Role-play session store backed by a replaceable repository."""

from datetime import datetime, timezone
from uuid import uuid4

from app.db.repositories import (
    RoleplaySessionRepository,
    SQLiteRoleplaySessionRepository,
)
from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessage,
    RoleplayMessageRole,
    RoleplayScenario,
    RoleplaySession,
)


class RoleplaySessionStore:
    """Coordinate role-play session creation and repository persistence."""

    def __init__(self, repository: RoleplaySessionRepository | None = None) -> None:
        self.repository = repository or SQLiteRoleplaySessionRepository()

    def create(
        self,
        user_id: str,
        scenario: RoleplayScenario,
        difficulty: int,
        opening_message: str,
        retrieved_guidance: RoleplayGuidance,
    ) -> RoleplaySession:
        """Create and store a new role-play session."""
        now = datetime.now(timezone.utc)
        session = RoleplaySession(
            session_id=str(uuid4()),
            user_id=user_id,
            scenario=scenario,
            difficulty=difficulty,
            retrieved_guidance=retrieved_guidance,
            messages=[
                RoleplayMessage(
                    role=RoleplayMessageRole.AGENT,
                    content=opening_message,
                    created_at=now,
                )
            ],
            created_at=now,
            updated_at=now,
        )
        return self.repository.save(session)

    def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        """Return a session only if it belongs to the user."""
        return self.repository.get_for_user(session_id, user_id)

    def append_message(
        self,
        session_id: str,
        user_id: str,
        role: RoleplayMessageRole,
        content: str,
    ) -> RoleplaySession | None:
        """Append a message and return the updated session."""
        now = datetime.now(timezone.utc)
        session = self.repository.get_for_user(session_id, user_id)
        if session is None:
            return None
        updated = session.model_copy(
            update={
                "messages": [
                    *session.messages,
                    RoleplayMessage(role=role, content=content, created_at=now),
                ],
                "updated_at": now,
            }
        )
        return self.repository.save(updated)


roleplay_session_store = RoleplaySessionStore()
