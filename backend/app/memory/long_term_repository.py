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
    MemoryType,
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

    async def create_memory(
        self,
        record: EpisodicMemoryRecord,
        *,
        reason_code: str,
    ) -> EpisodicMemoryRecord: ...

    async def get_memory(
        self,
        memory_id: str,
        user_id: str,
    ) -> EpisodicMemoryRecord | None: ...

    async def get_memory_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> EpisodicMemoryRecord | None: ...

    async def get_memory_by_content_hash(
        self,
        *,
        user_id: str,
        content_hash: str,
    ) -> EpisodicMemoryRecord | None: ...

    async def list_memories(
        self,
        user_id: str,
        *,
        statuses: tuple[MemoryRecordStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[EpisodicMemoryRecord]: ...

    async def search_memory_candidates(
        self,
        *,
        user_id: str,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        scenario_type: str | None,
        require_scenario_match: bool,
        query_terms: tuple[str, ...],
        now: datetime,
        limit: int = 50,
    ) -> list[EpisodicMemoryRecord]: ...

    async def record_retrieval(
        self,
        *,
        user_id: str,
        memory_ids: tuple[str, ...],
        retrieved_at: datetime,
        reason_code: str,
    ) -> int: ...

    async def transition_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        target_status: MemoryRecordStatus,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> EpisodicMemoryRecord: ...

    async def update_memory_summary(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        summary: str,
        content_hash: str,
        idempotency_key: str,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> EpisodicMemoryRecord: ...

    async def delete_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> None: ...

    async def save_checkpoint(
        self,
        checkpoint: PracticeThreadCheckpoint,
        *,
        expected_version: int | None,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> PracticeThreadCheckpoint: ...

    async def get_checkpoint(
        self,
        thread_id: str,
        user_id: str,
    ) -> PracticeThreadCheckpoint | None: ...

    async def list_checkpoints(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PracticeThreadCheckpoint]: ...

    async def list_events(
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

    async def create_memory(
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
                    scenario_id, practice_thread_id, skill_codes, context_tags,
                    source_type, source_id, evidence_type, confidence, status,
                    occurred_at, created_at, updated_at, last_retrieved_at,
                    expires_at, consent_version, content_hash, supersedes_id,
                    version, idempotency_key
                    ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                    )""",
                    _memory_values(record),
                )
                _insert_sqlite_event(connection, event)
        except sqlite3.IntegrityError as error:
            raise MemoryConflictError(
                f"episodic memory {record.memory_id!r} already exists"
            ) from error
        return record

    async def get_memory(
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

    async def get_memory_by_idempotency_key(
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

    async def get_memory_by_content_hash(
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

    async def list_memories(
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

    async def search_memory_candidates(
        self,
        *,
        user_id: str,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        scenario_type: str | None,
        require_scenario_match: bool,
        query_terms: tuple[str, ...],
        now: datetime,
        limit: int = 50,
    ) -> list[EpisodicMemoryRecord]:
        """Apply tenant, lifecycle, type, expiry and optional SQL text filters."""
        if not statuses or not memory_types:
            return []
        timestamp = _aware_now(now)
        bounded_limit = min(max(limit, 1), 100)
        status_placeholders = ", ".join("?" for _ in statuses)
        type_placeholders = ", ".join("?" for _ in memory_types)
        query = (
            "SELECT * FROM episodic_memories "
            "WHERE user_id = ? "
            f"AND status IN ({status_placeholders}) "
            f"AND memory_type IN ({type_placeholders}) "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        parameters: list[object] = [
            user_id,
            *(status.value for status in statuses),
            *(memory_type.value for memory_type in memory_types),
            timestamp.isoformat(),
        ]
        if require_scenario_match and scenario_type is not None:
            query += " AND (scenario_type = ? OR scenario_type IS NULL)"
            parameters.append(scenario_type)
        safe_terms = _safe_query_terms(query_terms)
        if safe_terms:
            query += " AND (" + " OR ".join(
                "lower(summary) LIKE ?" for _ in safe_terms
            ) + ")"
            parameters.extend(f"%{term}%" for term in safe_terms)
        query += (
            " ORDER BY occurred_at DESC, "
            "CASE WHEN last_retrieved_at IS NULL THEN 0 ELSE 1 END, "
            "last_retrieved_at ASC LIMIT ?"
        )
        parameters.append(bounded_limit)
        with connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_memory_from_row(row) for row in rows]

    async def record_retrieval(
        self,
        *,
        user_id: str,
        memory_ids: tuple[str, ...],
        retrieved_at: datetime,
        reason_code: str,
    ) -> int:
        """Atomically stamp bounded user-scoped hits and append content-free events."""
        timestamp = _aware_now(retrieved_at)
        unique_ids = tuple(dict.fromkeys(memory_ids))[:3]
        updated_count = 0
        with _sqlite_transaction() as connection:
            for memory_id in unique_ids:
                row = connection.execute(
                    """SELECT * FROM episodic_memories
                    WHERE memory_id = ? AND user_id = ?""",
                    (memory_id, user_id),
                ).fetchone()
                if row is None:
                    continue
                record = _memory_from_row(row)
                connection.execute(
                    """UPDATE episodic_memories
                    SET last_retrieved_at =
                        CASE
                            WHEN last_retrieved_at IS NULL
                              OR last_retrieved_at < ?
                            THEN ?
                            ELSE last_retrieved_at
                        END
                    WHERE memory_id = ? AND user_id = ?""",
                    (
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        memory_id,
                        user_id,
                    ),
                )
                _insert_sqlite_event(
                    connection,
                    MemoryEvent(
                        user_id=user_id,
                        subject_type=MemorySubjectType.EPISODIC_MEMORY,
                        subject_id=memory_id,
                        event_type=MemoryEventType.MEMORY_RETRIEVED,
                        from_status=record.status.value,
                        to_status=record.status.value,
                        reason_code=reason_code,
                        subject_version=record.version,
                        created_at=timestamp,
                    ),
                )
                updated_count += 1
        return updated_count

    async def transition_memory(
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

    async def update_memory_summary(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        summary: str,
        content_hash: str,
        idempotency_key: str,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> EpisodicMemoryRecord:
        """Update a safe summary with optimistic locking and content-free audit."""
        timestamp = _aware_now(changed_at)
        try:
            with _sqlite_transaction() as connection:
                row = connection.execute(
                    """SELECT * FROM episodic_memories
                    WHERE memory_id = ? AND user_id = ?""",
                    (memory_id, user_id),
                ).fetchone()
                if row is None:
                    raise MemoryNotFoundError(
                        "user-scoped episodic memory was not found"
                    )
                current = _memory_from_row(row)
                _require_expected_version(current.version, expected_version)
                updated = current.model_copy(
                    update={
                        "summary": summary,
                        "content_hash": content_hash,
                        "idempotency_key": idempotency_key,
                        "updated_at": timestamp,
                        "version": current.version + 1,
                    }
                )
                cursor = connection.execute(
                    """UPDATE episodic_memories
                    SET summary = ?, content_hash = ?, idempotency_key = ?,
                        updated_at = ?, version = ?
                    WHERE memory_id = ? AND user_id = ? AND version = ?""",
                    (
                        updated.summary,
                        updated.content_hash,
                        updated.idempotency_key,
                        updated.updated_at.isoformat(),
                        updated.version,
                        memory_id,
                        user_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise MemoryConflictError(
                        "episodic memory was changed concurrently"
                    )
                _insert_sqlite_event(
                    connection,
                    _memory_event(
                        record=updated,
                        event_type=MemoryEventType.MEMORY_UPDATED,
                        from_status=current.status,
                        to_status=current.status,
                        reason_code=reason_code,
                        created_at=timestamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise MemoryConflictError(
                "an equivalent episodic memory already exists"
            ) from error
        return updated

    async def delete_memory(
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

    async def save_checkpoint(
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
                        current_scenario, current_scenario_id,
                        current_scenario_summary, scenario_skill_codes,
                        helpful_strategy_codes,
                        attempted_skill_names, unresolved_next_step, status,
                        version, last_activity_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    current_scenario_id = ?, current_scenario_summary = ?,
                    scenario_skill_codes = ?,
                    helpful_strategy_codes = ?, attempted_skill_names = ?,
                    unresolved_next_step = ?, status = ?, version = ?,
                    last_activity_at = ?, updated_at = ?
                    WHERE thread_id = ? AND user_id = ? AND version = ?""",
                    (
                        saved.current_goal.value if saved.current_goal else None,
                        saved.current_stage,
                        saved.current_scenario,
                        saved.current_scenario_id,
                        saved.current_scenario_summary,
                        json.dumps(saved.scenario_skill_codes),
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

    async def get_checkpoint(
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

    async def list_checkpoints(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PracticeThreadCheckpoint]:
        """Return bounded user-owned checkpoints ordered by latest activity."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT * FROM thread_checkpoints
                WHERE user_id = ?
                ORDER BY last_activity_at DESC, thread_id ASC
                LIMIT ?""",
                (user_id, min(max(limit, 1), 500)),
            ).fetchall()
        return [_checkpoint_from_row(row) for row in rows]

    async def list_events(
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
        record.scenario_type,
        record.scenario_id,
        record.practice_thread_id,
        json.dumps([skill.value for skill in record.skill_codes]),
        json.dumps(record.context_tags),
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
        checkpoint.current_scenario,
        checkpoint.current_scenario_id,
        checkpoint.current_scenario_summary,
        json.dumps(checkpoint.scenario_skill_codes),
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
    data = dict(row)
    data["skill_codes"] = json.loads(data.get("skill_codes") or "[]")
    data["context_tags"] = json.loads(data.get("context_tags") or "[]")
    return EpisodicMemoryRecord.model_validate(data)


def _checkpoint_from_row(row: sqlite3.Row) -> PracticeThreadCheckpoint:
    data = dict(row)
    data["scenario_skill_codes"] = json.loads(
        data.get("scenario_skill_codes") or "[]"
    )
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


def _safe_query_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    """Bound application-generated LIKE terms and escape wildcard authority."""
    result: list[str] = []
    for term in terms[:16]:
        normalized = " ".join(term.casefold().split())
        if (
            2 <= len(normalized) <= 48
            and "%" not in normalized
            and "_" not in normalized
            and normalized not in result
        ):
            result.append(normalized)
    return tuple(result)


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
