"""PostgreSQL session-review repository implementation."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine
from app.models_session_review import SessionReviewRecord


class PostgresSessionReviewRepository:
    """PostgreSQL-backed privacy-safe session review repository."""

    def __init__(
        self, database_url: str | None = None, engine: AsyncEngine | None = None
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def save(self, record: SessionReviewRecord) -> SessionReviewRecord:
        """Persist one structured session review."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """INSERT INTO session_reviews
                    (review_id, user_id, source, source_id, payload, created_at)
                    VALUES
                    (:review_id, :user_id, :source, :source_id,
                    CAST(:payload AS jsonb), :created_at)
                    ON CONFLICT (review_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        source = EXCLUDED.source,
                        source_id = EXCLUDED.source_id,
                        payload = EXCLUDED.payload,
                        created_at = EXCLUDED.created_at"""
                ),
                {
                    "review_id": record.review_id,
                    "user_id": record.user_id,
                    "source": record.source,
                    "source_id": record.source_id,
                    "payload": record.model_dump_json(),
                    "created_at": record.created_at,
                },
            )
        return record

    async def list_for_user(
        self, user_id: str, limit: int = 20
    ) -> list[SessionReviewRecord]:
        """Return recent structured reviews for one user."""
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text(
                    """SELECT payload FROM session_reviews
                    WHERE user_id = :user_id
                    ORDER BY created_at DESC
                    LIMIT :limit"""
                ),
                {"user_id": user_id, "limit": limit},
            )).mappings().all()
        return [SessionReviewRecord.model_validate(row["payload"]) for row in rows]
