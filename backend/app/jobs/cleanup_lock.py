"""Single-run locks preventing duplicate retention work across scheduler replicas."""

from __future__ import annotations

from typing import Protocol

import psycopg
from psycopg import Connection

from app.db.config import database_settings
from app.db.providers import DatabaseProvider, resolve_database_provider


POSTGRES_CLEANUP_ADVISORY_LOCK_ID = 916_202_607_190_001


class CleanupRunLock(Protocol):
    """Minimal non-blocking lock contract used by the cleanup scheduler."""

    backend_name: str

    def acquire(self) -> bool: ...
    def release(self) -> None: ...


class NoopCleanupRunLock:
    """Single-process lock implementation for the local SQLite demo."""

    backend_name = "single_process"

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


class PostgresAdvisoryCleanupRunLock:
    """Hold one PostgreSQL session-level advisory lock for a cleanup iteration."""

    backend_name = "postgres_advisory_lock"

    def __init__(self, database_url: str, *, lock_id: int = POSTGRES_CLEANUP_ADVISORY_LOCK_ID) -> None:
        self.database_url = _psycopg_dsn(database_url)
        self.lock_id = lock_id
        self._connection: Connection | None = None

    def acquire(self) -> bool:
        """Try once without blocking; retain the connection only when acquired."""
        if self._connection is not None:
            return True
        connection = psycopg.connect(self.database_url, autocommit=True, connect_timeout=3)
        try:
            row = connection.execute(
                "SELECT pg_try_advisory_lock(%s)",
                (self.lock_id,),
            ).fetchone()
            acquired = bool(row and row[0])
            if acquired:
                self._connection = connection
                return True
        except Exception:
            connection.close()
            raise
        connection.close()
        return False

    def release(self) -> None:
        """Release the advisory lock and its owning database session."""
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            connection.execute("SELECT pg_advisory_unlock(%s)", (self.lock_id,)).fetchone()
        finally:
            connection.close()


def create_cleanup_run_lock(database_url: str | None = None) -> CleanupRunLock:
    """Select a distributed lock only for the shared PostgreSQL runtime."""
    resolved_url = database_url or database_settings().database_url
    if resolve_database_provider(resolved_url) == DatabaseProvider.POSTGRES:
        return PostgresAdvisoryCleanupRunLock(resolved_url)
    return NoopCleanupRunLock()


def _psycopg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    return database_url
