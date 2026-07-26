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
        _migrate_long_term_memory_columns(connection)
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
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_episodic_memories_user_idempotency
        ON episodic_memories(user_id, idempotency_key)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_episodic_memories_user_status
        ON episodic_memories(user_id, status, occurred_at)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_episodic_memories_user_hash
        ON episodic_memories(user_id, content_hash)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_episodic_memories_source
        ON episodic_memories(user_id, source_type, source_id)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_memory_events_user_created
        ON memory_events(user_id, created_at)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_memory_events_subject
        ON memory_events(user_id, subject_type, subject_id, created_at)"""
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


def _migrate_long_term_memory_columns(connection) -> None:
    """Upgrade existing local Phase 1 tables to the Phase 2 contract."""
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(episodic_memories)"
        ).fetchall()
    }
    if "idempotency_key" not in columns:
        connection.execute(
            "ALTER TABLE episodic_memories ADD COLUMN idempotency_key TEXT"
        )
        connection.execute(
            """UPDATE episodic_memories
            SET idempotency_key = lower(hex(randomblob(32)))
            WHERE idempotency_key IS NULL"""
        )
    episodic_sql = _sqlite_table_sql(connection, "episodic_memories")
    if "social_context" not in episodic_sql or "'chat'" not in episodic_sql:
        _rebuild_sqlite_episodic_memories(connection)
    event_sql = _sqlite_table_sql(connection, "memory_events")
    if "memory_proposal" not in event_sql:
        _rebuild_sqlite_memory_events(connection)


def _sqlite_table_sql(connection, table_name: str) -> str:
    """Return normalized SQLite DDL for one known application table."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return str(row["sql"] or "").casefold() if row else ""


def _rebuild_sqlite_episodic_memories(connection) -> None:
    """Expand Phase 1 checks while preserving every episodic record."""
    connection.execute(
        "ALTER TABLE episodic_memories RENAME TO episodic_memories_phase1"
    )
    connection.execute(
        """CREATE TABLE episodic_memories (
        memory_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        memory_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        scenario_type TEXT,
        source_type TEXT NOT NULL,
        source_id TEXT,
        evidence_type TEXT NOT NULL,
        confidence REAL NOT NULL,
        status TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_retrieved_at TEXT,
        expires_at TEXT,
        consent_version TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        supersedes_id TEXT,
        version INTEGER NOT NULL,
        CHECK (memory_type IN (
            'practice_experience', 'helpful_strategy', 'practice_milestone',
            'social_context', 'recurring_pattern'
        )),
        CHECK (source_type IN (
            'chat', 'roleplay', 'worksheet', 'exposure',
            'session_review', 'user_confirmed'
        )),
        CHECK (evidence_type IN (
            'explicit_user_statement', 'completed_product_action', 'user_confirmed'
        )),
        CHECK (confidence >= 0 AND confidence <= 1),
        CHECK (status IN ('active', 'inactive', 'archived', 'superseded', 'revoked')),
        CHECK (version >= 1)
        )"""
    )
    connection.execute(
        """INSERT INTO episodic_memories (
        memory_id, user_id, memory_type, summary, scenario_type,
        source_type, source_id, evidence_type, confidence, status,
        occurred_at, created_at, updated_at, last_retrieved_at,
        expires_at, consent_version, content_hash, idempotency_key,
        supersedes_id, version
        )
        SELECT
        memory_id, user_id, memory_type, summary, scenario_type,
        source_type, source_id, evidence_type, confidence, status,
        occurred_at, created_at, updated_at, last_retrieved_at,
        expires_at, consent_version, content_hash, idempotency_key,
        supersedes_id, version
        FROM episodic_memories_phase1"""
    )
    connection.execute("DROP TABLE episodic_memories_phase1")


def _rebuild_sqlite_memory_events(connection) -> None:
    """Expand audit subject checks without losing Phase 1 history."""
    connection.execute("ALTER TABLE memory_events RENAME TO memory_events_phase1")
    connection.execute(
        """CREATE TABLE memory_events (
        event_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        reason_code TEXT NOT NULL,
        subject_version INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        CHECK (subject_type IN (
            'episodic_memory', 'thread_checkpoint', 'memory_proposal'
        )),
        CHECK (subject_version >= 1)
        )"""
    )
    connection.execute(
        """INSERT INTO memory_events (
        event_id, user_id, subject_type, subject_id, event_type,
        from_status, to_status, reason_code, subject_version, created_at
        )
        SELECT
        event_id, user_id, subject_type, subject_id, event_type,
        from_status, to_status, reason_code, subject_version, created_at
        FROM memory_events_phase1"""
    )
    connection.execute("DROP TABLE memory_events_phase1")
