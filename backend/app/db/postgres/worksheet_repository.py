"""PostgreSQL worksheet repository implementation."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.config import database_settings
from app.db.postgres.engine import (
    postgres_read_connection,
    postgres_write_connection,
    shared_postgres_async_engine,
)
from app.models_worksheet import WorksheetRecord


class PostgresWorksheetRepository:
    """PostgreSQL-backed CBT-style worksheet repository."""

    def __init__(
        self, database_url: str | None = None, engine: AsyncEngine | None = None
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def save(self, record: WorksheetRecord) -> WorksheetRecord:
        """Persist one worksheet record."""
        async with postgres_write_connection(self.engine) as connection:
            await connection.execute(
                text(
                    """INSERT INTO worksheets
                    (worksheet_id, user_id, payload, created_at)
                    VALUES (:worksheet_id, :user_id, CAST(:payload AS jsonb), :created_at)
                    ON CONFLICT (worksheet_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        payload = EXCLUDED.payload,
                        created_at = EXCLUDED.created_at"""
                ),
                {
                    "worksheet_id": record.worksheet_id,
                    "user_id": record.user_id,
                    "payload": record.model_dump_json(),
                    "created_at": record.created_at,
                },
            )
        return record

    async def get(self, worksheet_id: str) -> WorksheetRecord | None:
        """Return one worksheet by id."""
        async with postgres_read_connection(self.engine) as connection:
            row = (await connection.execute(
                text("SELECT payload FROM worksheets WHERE worksheet_id = :worksheet_id"),
                {"worksheet_id": worksheet_id},
            )).mappings().first()
        return WorksheetRecord.model_validate(row["payload"]) if row else None
