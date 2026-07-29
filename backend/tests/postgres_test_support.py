"""Small PostgreSQL helpers for integration-test setup and assertions."""

from collections.abc import Mapping

from sqlalchemy import text

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine


async def execute_sql(
    statement: str,
    parameters: Mapping[str, object] | None = None,
) -> None:
    """Execute one PostgreSQL statement in a committed transaction."""
    engine = shared_postgres_async_engine(database_settings().database_url)
    async with engine.begin() as connection:
        await connection.execute(text(statement), parameters or {})


async def fetch_one(
    statement: str,
    parameters: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    """Return one mapping row from PostgreSQL."""
    engine = shared_postgres_async_engine(database_settings().database_url)
    async with engine.connect() as connection:
        return (
            await connection.execute(text(statement), parameters or {})
        ).mappings().first()
