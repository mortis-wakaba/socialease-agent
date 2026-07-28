"""PostgreSQL role-play session repository implementation."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.config import database_settings
from app.db.postgres.engine import (
    postgres_read_connection,
    postgres_write_connection,
    shared_postgres_async_engine,
)
from app.models_roleplay import RoleplaySession


class PostgresRoleplaySessionRepository:
    """PostgreSQL-backed role-play session repository."""

    def __init__(
        self, database_url: str | None = None, engine: AsyncEngine | None = None
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def save(self, session: RoleplaySession) -> RoleplaySession:
        """Persist one role-play session."""
        async with postgres_write_connection(self.engine) as connection:
            await connection.execute(
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

    async def get_for_user(
        self, session_id: str, user_id: str
    ) -> RoleplaySession | None:
        """Return a role-play session only if it belongs to the user."""
        async with postgres_read_connection(self.engine) as connection:
            row = (await connection.execute(
                text(
                    """SELECT payload FROM roleplay_sessions
                    WHERE session_id = :session_id AND user_id = :user_id"""
                ),
                {"session_id": session_id, "user_id": user_id},
            )).mappings().first()
        return RoleplaySession.model_validate(row["payload"]) if row else None

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RoleplaySession]:
        """Return recent role-play sessions for one user."""
        async with postgres_read_connection(self.engine) as connection:
            rows = (await connection.execute(
                text(
                    """SELECT payload FROM roleplay_sessions
                    WHERE user_id = :user_id
                    ORDER BY updated_at DESC
                    LIMIT :limit OFFSET :offset"""
                ),
                {"user_id": user_id, "limit": limit, "offset": offset},
            )).mappings().all()
        return [RoleplaySession.model_validate(row["payload"]) for row in rows]
