"""PostgreSQL adapter for retention-only physical deletion."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine


class PostgresRetentionRepository:
    """Execute bounded retention deletes inside PostgreSQL transactions."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def delete_trace_records_before(self, cutoff: datetime) -> int:
        return await self._delete(
            "DELETE FROM runs WHERE created_at <= :cutoff",
            cutoff,
        )

    async def delete_terminal_protocols_before(self, cutoff: datetime) -> int:
        return await self._delete(
            """DELETE FROM protocols
            WHERE status IN ('expired', 'rejected', 'consumed')
              AND updated_at <= :cutoff""",
            cutoff,
        )

    async def delete_terminal_intervention_plans_before(
        self,
        cutoff: datetime,
    ) -> int:
        return await self._delete(
            """DELETE FROM intervention_plans
            WHERE status IN ('completed', 'cancelled', 'blocked')
              AND updated_at <= :cutoff""",
            cutoff,
        )

    async def _delete(self, statement: str, cutoff: datetime) -> int:
        async with self.engine.begin() as connection:
            result = await connection.execute(text(statement), {"cutoff": cutoff})
            return result.rowcount or 0
