"""Role-play session store backed by a replaceable repository."""

from datetime import datetime, timezone
from uuid import uuid4

from app.db.repositories import (
    RoleplaySessionRepository,
    SQLiteRoleplaySessionRepository,
)
from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessageFeatures,
    RoleplaySession,
    RoleplaySessionStatus,
)
from app.models_scenario import ScenarioSpec


class RoleplaySessionStore:
    """Coordinate role-play session creation and repository persistence."""

    def __init__(self, repository: RoleplaySessionRepository | None = None) -> None:
        self.repository = repository or SQLiteRoleplaySessionRepository()

    async def create(
        self,
        user_id: str,
        scenario_spec: ScenarioSpec,
        difficulty: int,
        retrieved_guidance: RoleplayGuidance,
        *,
        session_id: str | None = None,
    ) -> RoleplaySession:
        """Create and store a new role-play session."""
        now = datetime.now(timezone.utc)
        session = RoleplaySession(
            session_id=session_id or str(uuid4()),
            user_id=user_id,
            scenario=None,
            scenario_spec=scenario_spec,
            difficulty=difficulty,
            retrieved_guidance=retrieved_guidance,
            messages=[],
            created_at=now,
            updated_at=now,
        )
        return await self.repository.save(session)

    async def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        """Return a session only if it belongs to the user."""
        return await self.repository.get_for_user(session_id, user_id)

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RoleplaySession]:
        """Return recent sessions for one user."""
        return await self.repository.list_for_user(
            user_id=user_id,
            limit=limit,
            offset=offset,
        )

    async def record_features(
        self,
        *,
        session_id: str,
        user_id: str,
        features: RoleplayMessageFeatures,
    ) -> RoleplaySession | None:
        """Persist privacy-safe turn features without duplicating transcript text."""
        session = await self.repository.get_for_user(session_id, user_id)
        if session is None:
            return None
        updated = session.model_copy(
            update={
                "practice_features": [
                    *session.practice_features,
                    features,
                ][-100:],
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        return await self.repository.save(updated)

    async def update_status(
        self,
        session_id: str,
        user_id: str,
        status: RoleplaySessionStatus,
    ) -> RoleplaySession | None:
        """Update a role-play session lifecycle status."""
        now = datetime.now(timezone.utc)
        session = await self.repository.get_for_user(session_id, user_id)
        if session is None:
            return None
        updated = session.model_copy(
            update={
                "status": status,
                "updated_at": now,
            }
        )
        return await self.repository.save(updated)


roleplay_session_store = RoleplaySessionStore()
