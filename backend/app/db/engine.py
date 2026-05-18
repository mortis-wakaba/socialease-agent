"""SQLite connection helpers for local development persistence."""

from pathlib import Path
import os
import sqlite3

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "socialease.db"


def database_path() -> Path:
    """Return the configured SQLite database path."""
    return Path(os.getenv("SOCIALEASE_DB_PATH", DEFAULT_DB_PATH))


def connect() -> sqlite3.Connection:
    """Create one SQLite connection with row access by column name."""
    connection = sqlite3.connect(database_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection
