"""Single-run locks preventing duplicate retention work across scheduler replicas."""

from __future__ import annotations

from typing import Protocol

import psycopg
from psycopg import AsyncConnection

from app.db.config import database_settings


POSTGRES_CLEANUP_ADVISORY_LOCK_ID = 916_202_607_190_001


class CleanupRunLock(Protocol):
    """Minimal non-blocking lock contract used by the cleanup scheduler."""

    backend_name: str

    async def acquire(self) -> bool: ...
    async def release(self) -> None: ...


class PostgresAdvisoryCleanupRunLock:
    """Hold one PostgreSQL session-level advisory lock for a cleanup iteration."""

    backend_name = "postgres_advisory_lock"

    def __init__(self, database_url: str, *, lock_id: int = POSTGRES_CLEANUP_ADVISORY_LOCK_ID) -> None:
        self.database_url = _psycopg_dsn(database_url)
        self.lock_id = lock_id
        self._connection: AsyncConnection | None = None

    async def acquire(self) -> bool:
        """Try once without blocking; retain the connection only when acquired."""
        if self._connection is not None:
            return True
        connection = await psycopg.AsyncConnection.connect(
            self.database_url, autocommit=True, connect_timeout=3
        )
        try:
            row = await (
                await connection.execute(
                "SELECT pg_try_advisory_lock(%s)",
                (self.lock_id,),
                )
            ).fetchone()
            acquired = bool(row and row[0])
            if acquired:
                self._connection = connection
                return True
        except Exception:
            await connection.close()
            raise
        await connection.close()
        return False

    async def release(self) -> None:
        """Release the advisory lock and its owning database session."""
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            await (
                await connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (self.lock_id,)
                )
            ).fetchone()
        finally:
            await connection.close()


def create_cleanup_run_lock(database_url: str | None = None) -> CleanupRunLock:
    """Return the required distributed PostgreSQL cleanup lock."""
    resolved_url = database_url or database_settings().database_url
    return PostgresAdvisoryCleanupRunLock(resolved_url)


def _psycopg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    return database_url
