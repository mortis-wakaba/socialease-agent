"""Database connection helpers for local development persistence."""

import sqlite3

from app.db.config import database_settings


def database_path():
    """Return the configured SQLite database path."""
    return database_settings().sqlite_path


def connect() -> sqlite3.Connection:
    """Create one SQLite connection with row access by column name."""
    settings = database_settings()
    if settings.provider not in {"sqlite", "file"}:
        raise NotImplementedError(
            "app.db.engine.connect() is the SQLite-only connection helper; "
            "PostgreSQL runtime callers must obtain repositories from RepositoryFactory. "
            f"Received provider {settings.provider!r}."
        )
    connection = sqlite3.connect(
        database_path(),
        check_same_thread=False,
        timeout=settings.sqlite_timeout_seconds,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection
