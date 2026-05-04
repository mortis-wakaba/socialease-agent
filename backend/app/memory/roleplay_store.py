"""In-memory role-play session store with a replaceable repository shape."""

from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessage,
    RoleplayMessageRole,
    RoleplayScenario,
    RoleplaySession,
)


class RoleplaySessionStore:
    """Store role-play sessions in memory for the MVP."""

    def __init__(self) -> None:
        self._sessions: dict[str, RoleplaySession] = {}
        self._lock = Lock()

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
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        """Return a session only if it belongs to the user."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.user_id != user_id:
                return None
            return session

    def append_message(
        self,
        session_id: str,
        user_id: str,
        role: RoleplayMessageRole,
        content: str,
    ) -> RoleplaySession | None:
        """Append a message and return the updated session."""
        now = datetime.now(timezone.utc)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.user_id != user_id:
                return None

            updated = session.model_copy(
                update={
                    "messages": [
                        *session.messages,
                        RoleplayMessage(
                            role=role,
                            content=content,
                            created_at=now,
                        ),
                    ],
                    "updated_at": now,
                }
            )
            self._sessions[session_id] = updated
            return updated


roleplay_session_store = RoleplaySessionStore()
