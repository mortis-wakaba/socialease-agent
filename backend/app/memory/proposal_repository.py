"""Persistence contract and SQLite adapter for confirmation-gated proposals."""

from datetime import datetime
import sqlite3
from typing import Protocol

from app.db.engine import connect
from app.db.session import initialize_database
from app.memory.long_term_repository import (
    MemoryConflictError,
    _insert_sqlite_event,
    _sqlite_transaction,
)
from app.models_long_term_memory import (
    MemoryEvent,
    MemoryEventType,
    MemoryProposalStatus,
    MemorySubjectType,
    PendingMemoryProposalRecord,
)


class MemoryProposalRepository(Protocol):
    """Persistence contract for safe proposals awaiting confirmation."""

    def save_pending(
        self,
        record: PendingMemoryProposalRecord,
    ) -> PendingMemoryProposalRecord: ...

    def get_for_user(
        self,
        proposal_id: str,
        user_id: str,
    ) -> PendingMemoryProposalRecord | None: ...

    def get_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> PendingMemoryProposalRecord | None: ...

    def list_pending(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PendingMemoryProposalRecord]: ...

    def record_rejection(
        self,
        *,
        user_id: str,
        proposal_id: str,
        reason_code: str,
        created_at: datetime,
    ) -> None: ...


class SQLiteMemoryProposalRepository:
    """SQLite adapter for bounded, safe pending proposal records."""

    def __init__(self) -> None:
        initialize_database()

    def save_pending(
        self,
        record: PendingMemoryProposalRecord,
    ) -> PendingMemoryProposalRecord:
        """Persist one pending candidate and content-free event atomically."""
        if (
            record.status != MemoryProposalStatus.PENDING_CONFIRMATION
            or record.version != 1
        ):
            raise MemoryConflictError(
                "new memory proposal must be pending_confirmation at version 1"
            )
        try:
            with _sqlite_transaction() as connection:
                connection.execute(
                    """INSERT INTO memory_proposals (
                    proposal_id, user_id, memory_type, summary, scenario_type,
                    source_type, source_id, evidence_type, confidence,
                    occurred_at, status, policy_reason, content_hash,
                    idempotency_key, version, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.proposal_id,
                        record.user_id,
                        record.memory_type.value,
                        record.summary,
                        record.scenario_type.value if record.scenario_type else None,
                        record.source_type.value,
                        record.source_id,
                        record.evidence_type.value,
                        record.confidence,
                        record.occurred_at.isoformat(),
                        record.status.value,
                        record.policy_reason.value,
                        record.content_hash,
                        record.idempotency_key,
                        record.version,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                        record.expires_at.isoformat(),
                    ),
                )
                _insert_sqlite_event(
                    connection,
                    MemoryEvent(
                        user_id=record.user_id,
                        subject_type=MemorySubjectType.MEMORY_PROPOSAL,
                        subject_id=record.proposal_id,
                        event_type=MemoryEventType.PROPOSAL_CREATED,
                        from_status=None,
                        to_status=record.status.value,
                        reason_code=record.policy_reason.value,
                        subject_version=record.version,
                        created_at=record.created_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise MemoryConflictError("memory proposal already exists") from error
        return record

    def get_for_user(
        self,
        proposal_id: str,
        user_id: str,
    ) -> PendingMemoryProposalRecord | None:
        """Return one proposal only when its owner matches."""
        with connect() as connection:
            row = connection.execute(
                """SELECT * FROM memory_proposals
                WHERE proposal_id = ? AND user_id = ?""",
                (proposal_id, user_id),
            ).fetchone()
        return PendingMemoryProposalRecord.model_validate(dict(row)) if row else None

    def get_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> PendingMemoryProposalRecord | None:
        """Resolve a safely retried proposal write."""
        with connect() as connection:
            row = connection.execute(
                """SELECT * FROM memory_proposals
                WHERE user_id = ? AND idempotency_key = ?""",
                (user_id, idempotency_key),
            ).fetchone()
        return PendingMemoryProposalRecord.model_validate(dict(row)) if row else None

    def list_pending(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PendingMemoryProposalRecord]:
        """Return unexpired pending proposals for one user."""
        bounded_limit = min(max(limit, 1), 500)
        with connect() as connection:
            rows = connection.execute(
                """SELECT * FROM memory_proposals
                WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
                LIMIT ?""",
                (
                    user_id,
                    MemoryProposalStatus.PENDING_CONFIRMATION.value,
                    bounded_limit,
                ),
            ).fetchall()
        return [
            PendingMemoryProposalRecord.model_validate(dict(row))
            for row in rows
        ]

    def record_rejection(
        self,
        *,
        user_id: str,
        proposal_id: str,
        reason_code: str,
        created_at: datetime,
    ) -> None:
        """Audit a rejection without persisting rejected candidate content."""
        with _sqlite_transaction() as connection:
            _insert_sqlite_event(
                connection,
                MemoryEvent(
                    user_id=user_id,
                    subject_type=MemorySubjectType.MEMORY_PROPOSAL,
                    subject_id=proposal_id,
                    event_type=MemoryEventType.PROPOSAL_REJECTED,
                    from_status=None,
                    to_status=MemoryProposalStatus.REJECTED.value,
                    reason_code=reason_code,
                    subject_version=1,
                    created_at=created_at,
                ),
            )
