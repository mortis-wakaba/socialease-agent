"""PostgreSQL role-play session repository implementation."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models_roleplay import RoleplaySession


class PostgresRoleplaySessionRepository:
    """PostgreSQL-backed role-play session repository."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def save(self, session: RoleplaySession) -> RoleplaySession:
        """Persist one role-play session."""
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO roleplay_sessions
                    (session_id, user_id, scenario, difficulty, payload, created_at, updated_at)
                    VALUES
                    (:session_id, :user_id, :scenario, :difficulty,
                    CAST(:payload AS jsonb), :created_at, :updated_at)
                    ON CONFLICT (session_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        scenario = EXCLUDED.scenario,
                        difficulty = EXCLUDED.difficulty,
                        payload = EXCLUDED.payload,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at"""
                ),
                {
                    "session_id": session.session_id,
                    "user_id": session.user_id,
                    "scenario": session.scenario,
                    "difficulty": session.difficulty,
                    "payload": session.model_dump_json(),
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                },
            )
        return session

    def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        """Return a role-play session only if it belongs to the user."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM roleplay_sessions
                    WHERE session_id = :session_id AND user_id = :user_id"""
                ),
                {"session_id": session_id, "user_id": user_id},
            ).mappings().first()
        return RoleplaySession.model_validate(row["payload"]) if row else None

    def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RoleplaySession]:
        """Return recent role-play sessions for one user."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM roleplay_sessions
                    WHERE user_id = :user_id
                    ORDER BY updated_at DESC
                    LIMIT :limit OFFSET :offset"""
                ),
                {"user_id": user_id, "limit": limit, "offset": offset},
            ).mappings().all()
        return [RoleplaySession.model_validate(row["payload"]) for row in rows]
