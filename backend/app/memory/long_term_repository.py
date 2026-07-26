"""Durable memory repository contract and transactional SQLite adapter."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterator, Protocol

from app.db.engine import connect
from app.db.session import initialize_database
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvent,
    MemoryEventType,
    MemoryRecordStatus,
    MemorySubjectType,
    PracticeThreadCheckpoint,
)


class MemoryRepositoryError(RuntimeError):
    """Base error for durable memory persistence."""


class MemoryConflictError(MemoryRepositoryError):
    """Raised for duplicate creation or optimistic-lock conflicts."""


class MemoryNotFoundError(MemoryRepositoryError):
    """Raised when a user-scoped durable memory subject does not exist."""


class InvalidMemoryTransitionError(MemoryRepositoryError):
    """Raised when a lifecycle state transition is not allowed."""


class LongTermMemoryRepository(Protocol):
    """Persistence contract shared by SQLite and PostgreSQL adapters."""

    def create_memory(
        self,
        record: EpisodicMemoryRecord,
        *,
        reason_code: str,
    ) -> EpisodicMemoryRecord: ...

    def get_memory(
        self,
        memory_id: str,
        user_id: str,
    ) -> EpisodicMemoryRecord | None: ...

    def get_memory_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> EpisodicMemoryRecord | None: ...

    def get_memory_by_content_hash(
        self,
        *,
        user_id: str,
        content_hash: str,
    ) -> EpisodicMemoryRecord | None: ...

    def list_memories(
        self,
        user_id: str,
        *,
        statuses: tuple[MemoryRecordStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[EpisodicMemoryRecord]: ...

    def transition_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        target_status: MemoryRecordStatus,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> EpisodicMemoryRecord: ...

    def delete_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> None: ...

    def save_checkpoint(
        self,
        checkpoint: PracticeThreadCheckpoint,
        *,
        expected_version: int | None,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> PracticeThreadCheckpoint: ...

    def get_checkpoint(
        self,
        thread_id: str,
        user_id: str,
    ) -> PracticeThreadCheckpoint | None: ...

    def list_events(
        self,
        *,
        user_id: str,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryEvent]: ...


_ALLOWED_MEMORY_TRANSITIONS: dict[MemoryRecordStatus, set[MemoryRecordStatus]] = {
    MemoryRecordStatus.ACTIVE: {
        MemoryRecordStatus.INACTIVE,
        MemoryRecordStatus.ARCHIVED,
        MemoryRecordStatus.SUPERSEDED,
        MemoryRecordStatus.REVOKED,
    },
    MemoryRecordStatus.INACTIVE: {
        MemoryRecordStatus.ACTIVE,
        MemoryRecordStatus.ARCHIVED,
        MemoryRecordStatus.SUPERSEDED,
        MemoryRecordStatus.REVOKED,
    },
    MemoryRecordStatus.ARCHIVED: {
        MemoryRecordStatus.ACTIVE,
        MemoryRecordStatus.SUPERSEDED,
        MemoryRecordStatus.REVOKED,
    },
    MemoryRecordStatus.SUPERSEDED: set(),
    MemoryRecordStatus.REVOKED: set(),
}

_TRANSITION_EVENTS: dict[MemoryRecordStatus, MemoryEventType] = {
    MemoryRecordStatus.ACTIVE: MemoryEventType.MEMORY_REACTIVATED,
    MemoryRecordStatus.INACTIVE: MemoryEventType.MEMORY_INACTIVATED,
    MemoryRecordStatus.ARCHIVED: MemoryEventType.MEMORY_ARCHIVED,
    MemoryRecordStatus.SUPERSEDED: MemoryEventType.MEMORY_SUPERSEDED,
    MemoryRecordStatus.REVOKED: MemoryEventType.MEMORY_REVOKED,
}


class SQLiteLongTermMemoryRepository:
    """SQLite adapter with user-scoped SQL and atomic audit events."""

    def __init__(self) -> None:
        initialize_database()

    def create_memory(
        self,
        record: EpisodicMemoryRecord,
        *,
        reason_code: str,
    ) -> EpisodicMemoryRecord:
        """Create an active episodic memory and its audit event atomically."""
        if record.version != 1 or record.status != MemoryRecordStatus.ACTIVE:
            raise MemoryConflictError("new episodic memory must be active at version 1")
        event = _memory_event(
            record=record,
            event_type=MemoryEventType.MEMORY_COMMITTED,
            from_status=None,
            to_status=record.status,
            reason_code=reason_code,
            created_at=record.created_at,
        )
        try:
            with _sqlite_transaction() as connection:
                connection.execute(
                    """INSERT INTO episodic_memories (
                    memory_id, user_id, memory_type, summary, scenario_type,
                    source_type, source_id, evidence_type, confidence, status,
                    occurred_at, created_at, updated_at, last_retrieved_at,
                    expires_at, consent_version, content_hash, supersedes_id,
                    version, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _memory_values(record),
                )
                _insert_sqlite_event(connection, event)
        except sqlite3.IntegrityError as error:
            raise MemoryConflictError(
                f"episodic memory {record.memory_id!r} already exists"
            ) from error
        return record

    def get_memory(
        self,
        memory_id: str,
        user_id: str,
    ) -> EpisodicMemoryRecord | None:
        """Return one memory only when both identifier and owner match."""
        with connect() as connection:
            row = connection.execute(
                """SELECT * FROM episodic_memories
                WHERE memory_id = ? AND user_id = ?""",
                (memory_id, user_id),
            ).fetchone()
        return _memory_from_row(row) if row else None

    def get_memory_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> EpisodicMemoryRecord | None:
        """Resolve a safely retried write inside the same user scope."""
        with connect() as connection:
            row = connection.execute(
                """SELECT * FROM episodic_memories
                WHERE user_id = ? AND idempotency_key = ?""",
                (user_id, idempotency_key),
            ).fetchone()
        return _memory_from_row(row) if row else None

    def get_memory_by_content_hash(
        self,
        *,
        user_id: str,
        content_hash: str,
    ) -> EpisodicMemoryRecord | None:
        """Return one exact-content match, preferring a currently usable record."""
        with connect() as connection:
            row = connection.execute(
                """SELECT * FROM episodic_memories
                WHERE user_id = ? AND content_hash = ?
                ORDER BY
                    CASE status
                        WHEN 'active' THEN 0
                        WHEN 'inactive' THEN 1
                        WHEN 'archived' THEN 2
                        WHEN 'revoked' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC
                LIMIT 1""",
                (user_id, content_hash),
            ).fetchone()
        return _memory_from_row(row) if row else None

    def list_memories(
        self,
        user_id: str,
        *,
        statuses: tuple[MemoryRecordStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[EpisodicMemoryRecord]:
        """Return bounded user-owned memories ordered by occurrence time."""
        bounded_limit = min(max(limit, 1), 500)
        parameters: list[object] = [user_id]
        status_clause = ""
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            status_clause = f" AND status IN ({placeholders})"
            parameters.extend(status.value for status in statuses)
        parameters.append(bounded_limit)
        with connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM episodic_memories
                WHERE user_id = ?{status_clause}
                ORDER BY occurred_at DESC, created_at DESC
                LIMIT ?""",
                parameters,
            ).fetchall()
        return [_memory_from_row(row) for row in rows]

    def transition_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        target_status: MemoryRecordStatus,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> EpisodicMemoryRecord:
        """Apply one valid compare-and-swap lifecycle transition."""
        timestamp = _aware_now(changed_at)
        with _sqlite_transaction() as connection:
            row = connection.execute(
                """SELECT * FROM episodic_memories
                WHERE memory_id = ? AND user_id = ?""",
                (memory_id, user_id),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError("user-scoped episodic memory was not found")
            current = _memory_from_row(row)
            _require_expected_version(current.version, expected_version)
            _require_memory_transition(current.status, target_status)
            updated = current.model_copy(
                update={
                    "status": target_status,
                    "updated_at": timestamp,
                    "version": current.version + 1,
                }
            )
            cursor = connection.execute(
                """UPDATE episodic_memories
                SET status = ?, updated_at = ?, version = ?
                WHERE memory_id = ? AND user_id = ? AND version = ?""",
                (
                    updated.status.value,
                    updated.updated_at.isoformat(),
                    updated.version,
                    memory_id,
                    user_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise MemoryConflictError("episodic memory was changed concurrently")
            _insert_sqlite_event(
                connection,
                _memory_event(
                    record=updated,
                    event_type=_TRANSITION_EVENTS[target_status],
                    from_status=current.status,
                    to_status=target_status,
                    reason_code=reason_code,
                    created_at=timestamp,
                ),
            )
        return updated

    def delete_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> None:
        """Physically delete memory content while retaining a content-free event."""
        timestamp = _aware_now(changed_at)
        with _sqlite_transaction() as connection:
            row = connection.execute(
                """SELECT * FROM episodic_memories
                WHERE memory_id = ? AND user_id = ?""",
                (memory_id, user_id),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError("user-scoped episodic memory was not found")
            current = _memory_from_row(row)
            _require_expected_version(current.version, expected_version)
            event = _memory_event(
                record=current.model_copy(update={"version": current.version + 1}),
                event_type=MemoryEventType.MEMORY_DELETED,
                from_status=current.status,
                to_status=None,
                reason_code=reason_code,
                created_at=timestamp,
            )
            cursor = connection.execute(
                """DELETE FROM episodic_memories
                WHERE memory_id = ? AND user_id = ? AND version = ?""",
                (memory_id, user_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise MemoryConflictError("episodic memory was changed concurrently")
            _insert_sqlite_event(connection, event)

    def save_checkpoint(
        self,
        checkpoint: PracticeThreadCheckpoint,
        *,
        expected_version: int | None,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> PracticeThreadCheckpoint:
        """Create or compare-and-swap a user-scoped thread checkpoint."""
        timestamp = _aware_now(changed_at or checkpoint.updated_at)
        with _sqlite_transaction() as connection:
            row = connection.execute(
                """SELECT * FROM thread_checkpoints
                WHERE thread_id = ? AND user_id = ?""",
                (checkpoint.thread_id, checkpoint.user_id),
            ).fetchone()
            current = _checkpoint_from_row(row) if row else None
            if current is None:
                if expected_version is not None or checkpoint.version != 1:
                    raise MemoryConflictError(
                        "new checkpoint requires expected_version=None and version=1"
                    )
                saved = checkpoint
                try:
                    connection.execute(
                        """INSERT INTO thread_checkpoints (
                        thread_id, user_id, current_goal, current_stage,
                        current_scenario, helpful_strategy_codes,
                        attempted_skill_names, unresolved_next_step, status,
                        version, last_activity_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        _checkpoint_values(saved),
                    )
                except sqlite3.IntegrityError as error:
                    raise MemoryConflictError(
                        f"checkpoint {checkpoint.thread_id!r} already exists"
                    ) from error
                from_status = None
            else:
                if expected_version is None:
                    raise MemoryConflictError("existing checkpoint requires a version")
                _require_expected_version(current.version, expected_version)
                saved = checkpoint.model_copy(
                    update={
                        "created_at": current.created_at,
                        "updated_at": timestamp,
                        "version": current.version + 1,
                    }
                )
                cursor = connection.execute(
                    """UPDATE thread_checkpoints SET
                    current_goal = ?, current_stage = ?, current_scenario = ?,
                    helpful_strategy_codes = ?, attempted_skill_names = ?,
                    unresolved_next_step = ?, status = ?, version = ?,
                    last_activity_at = ?, updated_at = ?
                    WHERE thread_id = ? AND user_id = ? AND version = ?""",
                    (
                        saved.current_goal.value if saved.current_goal else None,
                        saved.current_stage,
                        saved.current_scenario.value if saved.current_scenario else None,
                        json.dumps(saved.helpful_strategy_codes),
                        json.dumps(saved.attempted_skill_names),
                        saved.unresolved_next_step,
                        saved.status.value,
                        saved.version,
                        saved.last_activity_at.isoformat(),
                        saved.updated_at.isoformat(),
                        saved.thread_id,
                        saved.user_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise MemoryConflictError("checkpoint was changed concurrently")
                from_status = current.status.value
            _insert_sqlite_event(
                connection,
                MemoryEvent(
                    user_id=saved.user_id,
                    subject_type=MemorySubjectType.THREAD_CHECKPOINT,
                    subject_id=saved.thread_id,
                    event_type=MemoryEventType.CHECKPOINT_UPDATED,
                    from_status=from_status,
                    to_status=saved.status.value,
                    reason_code=reason_code,
                    subject_version=saved.version,
                    created_at=timestamp,
                ),
            )
        return saved

    def get_checkpoint(
        self,
        thread_id: str,
        user_id: str,
    ) -> PracticeThreadCheckpoint | None:
        """Return a checkpoint only when both thread and owner match."""
        with connect() as connection:
            row = connection.execute(
                """SELECT * FROM thread_checkpoints
                WHERE thread_id = ? AND user_id = ?""",
                (thread_id, user_id),
            ).fetchone()
        return _checkpoint_from_row(row) if row else None

    def list_events(
        self,
        *,
        user_id: str,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryEvent]:
        """Return content-free audit events within one user scope."""
        bounded_limit = min(max(limit, 1), 500)
        query = "SELECT * FROM memory_events WHERE user_id = ?"
        parameters: list[object] = [user_id]
        if subject_id is not None:
            query += " AND subject_id = ?"
            parameters.append(subject_id)
        query += " ORDER BY created_at ASC, event_id ASC LIMIT ?"
        parameters.append(bounded_limit)
        with connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_event_from_row(row) for row in rows]


@contextmanager
def _sqlite_transaction() -> Iterator[sqlite3.Connection]:
    """Run a SQLite transaction that serializes lifecycle writers."""
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _memory_values(record: EpisodicMemoryRecord) -> tuple[object, ...]:
    return (
        record.memory_id,
        record.user_id,
        record.memory_type.value,
        record.summary,
        record.scenario_type.value if record.scenario_type else None,
        record.source_type.value,
        record.source_id,
        record.evidence_type.value,
        record.confidence,
        record.status.value,
        record.occurred_at.isoformat(),
        record.created_at.isoformat(),
        record.updated_at.isoformat(),
        record.last_retrieved_at.isoformat() if record.last_retrieved_at else None,
        record.expires_at.isoformat() if record.expires_at else None,
        record.consent_version,
        record.content_hash,
        record.supersedes_id,
        record.version,
        record.idempotency_key,
    )


def _checkpoint_values(checkpoint: PracticeThreadCheckpoint) -> tuple[object, ...]:
    return (
        checkpoint.thread_id,
        checkpoint.user_id,
        checkpoint.current_goal.value if checkpoint.current_goal else None,
        checkpoint.current_stage,
        checkpoint.current_scenario.value if checkpoint.current_scenario else None,
        json.dumps(checkpoint.helpful_strategy_codes),
        json.dumps(checkpoint.attempted_skill_names),
        checkpoint.unresolved_next_step,
        checkpoint.status.value,
        checkpoint.version,
        checkpoint.last_activity_at.isoformat(),
        checkpoint.created_at.isoformat(),
        checkpoint.updated_at.isoformat(),
    )


def _memory_from_row(row: sqlite3.Row) -> EpisodicMemoryRecord:
    return EpisodicMemoryRecord.model_validate(dict(row))


def _checkpoint_from_row(row: sqlite3.Row) -> PracticeThreadCheckpoint:
    data = dict(row)
    data["helpful_strategy_codes"] = json.loads(data["helpful_strategy_codes"])
    data["attempted_skill_names"] = json.loads(data["attempted_skill_names"])
    return PracticeThreadCheckpoint.model_validate(data)


def _event_from_row(row: sqlite3.Row) -> MemoryEvent:
    return MemoryEvent.model_validate(dict(row))


def _insert_sqlite_event(
    connection: sqlite3.Connection,
    event: MemoryEvent,
) -> None:
    connection.execute(
        """INSERT INTO memory_events (
        event_id, user_id, subject_type, subject_id, event_type,
        from_status, to_status, reason_code, subject_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.event_id,
            event.user_id,
            event.subject_type.value,
            event.subject_id,
            event.event_type.value,
            event.from_status,
            event.to_status,
            event.reason_code,
            event.subject_version,
            event.created_at.isoformat(),
        ),
    )


def _memory_event(
    *,
    record: EpisodicMemoryRecord,
    event_type: MemoryEventType,
    from_status: MemoryRecordStatus | None,
    to_status: MemoryRecordStatus | None,
    reason_code: str,
    created_at: datetime,
) -> MemoryEvent:
    return MemoryEvent(
        user_id=record.user_id,
        subject_type=MemorySubjectType.EPISODIC_MEMORY,
        subject_id=record.memory_id,
        event_type=event_type,
        from_status=from_status.value if from_status else None,
        to_status=to_status.value if to_status else None,
        reason_code=reason_code,
        subject_version=record.version,
        created_at=created_at,
    )


def _require_expected_version(actual: int, expected: int) -> None:
    if actual != expected:
        raise MemoryConflictError(
            f"version conflict: expected {expected}, found {actual}"
        )


def _require_memory_transition(
    current: MemoryRecordStatus,
    target: MemoryRecordStatus,
) -> None:
    if target not in _ALLOWED_MEMORY_TRANSITIONS[current]:
        raise InvalidMemoryTransitionError(
            f"cannot transition episodic memory from {current.value} to {target.value}"
        )


def _aware_now(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("durable memory timestamps must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
