"""PostgreSQL worksheet repository implementation."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models_worksheet import WorksheetRecord


class PostgresWorksheetRepository:
    """PostgreSQL-backed CBT-style worksheet repository."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def save(self, record: WorksheetRecord) -> WorksheetRecord:
        """Persist one worksheet record."""
        with self.engine.begin() as connection:
            connection.execute(
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

    def get(self, worksheet_id: str) -> WorksheetRecord | None:
        """Return one worksheet by id."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT payload FROM worksheets WHERE worksheet_id = :worksheet_id"),
                {"worksheet_id": worksheet_id},
            ).mappings().first()
        return WorksheetRecord.model_validate(row["payload"]) if row else None
