"""Schema initialization helpers for database-backed repositories."""

from app.db.engine import connect
from app.db.models import SCHEMA


def initialize_database() -> None:
    """Create local SQLite tables when they do not exist yet."""
    with connect() as connection:
        connection.executescript(SCHEMA)
        _migrate_protocol_columns(connection)
        _migrate_auth_tables(connection)
        _migrate_user_audit_columns(connection)
        _create_post_migration_indexes(connection)


def _migrate_protocol_columns(connection) -> None:
    """Add protocol lifecycle columns to existing local SQLite files."""
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(protocols)").fetchall()
    }
    migrations = {
        "session_id": "ALTER TABLE protocols ADD COLUMN session_id TEXT",
        "harness_action": "ALTER TABLE protocols ADD COLUMN harness_action TEXT",
        "request_hash": "ALTER TABLE protocols ADD COLUMN request_hash TEXT",
        "expires_at": "ALTER TABLE protocols ADD COLUMN expires_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _create_post_migration_indexes(connection) -> None:
    """Create indexes that depend on migrated columns."""
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_protocols_expiration ON protocols(status, expires_at)"
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_protocols_action_hash
        ON protocols(user_id, harness_action, request_hash)"""
    )


def _migrate_auth_tables(connection) -> None:
    """Create auth tables for existing local SQLite files."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        last_login_at TEXT,
        last_failed_login_at TEXT,
        failed_login_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS user_sessions (
        session_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        refresh_token_hash TEXT NOT NULL UNIQUE,
        access_token_id TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
        )"""
    )


def _migrate_user_audit_columns(connection) -> None:
    """Add auth audit columns to existing local SQLite files."""
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    migrations = {
        "last_login_at": "ALTER TABLE users ADD COLUMN last_login_at TEXT",
        "last_failed_login_at": "ALTER TABLE users ADD COLUMN last_failed_login_at TEXT",
        "failed_login_count": (
            "ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0"
        ),
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)
