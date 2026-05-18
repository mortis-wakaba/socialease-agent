"""Schema initialization helpers for database-backed repositories."""

from app.db.engine import connect
from app.db.models import SCHEMA


def initialize_database() -> None:
    """Create local SQLite tables when they do not exist yet."""
    with connect() as connection:
        connection.executescript(SCHEMA)
