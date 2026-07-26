"""PostgreSQL adapter for confirmation-gated memory proposals."""

from datetime import datetime
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_engine
from app.memory.long_term_repository import MemoryConflictError
from app.memory.proposal_repository import MemoryProposalRepository
from app.models_long_term_memory import (
    MemoryEvent,
    MemoryEventType,
    MemoryProposalStatus,
    MemorySubjectType,
    PendingMemoryProposalRecord,
)


class PostgresMemoryProposalRepository(MemoryProposalRepository):
    """PostgreSQL adapter with atomic content-free proposal events."""

    def __init__(
        self,
        database_url: str | None = None,
        engine: Engine | None = None,
    ) -> None:
        self.engine = engine or shared_postgres_engine(
            database_url or database_settings().database_url
        )

    def save_pending(
        self,
        record: PendingMemoryProposalRecord,
    ) -> PendingMemoryProposalRecord:
        """Persist one pending candidate and audit event atomically."""
        if (
            record.status != MemoryProposalStatus.PENDING_CONFIRMATION
            or record.version != 1
        ):
            raise MemoryConflictError(
                "new memory proposal must be pending_confirmation at version 1"
            )
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        """INSERT INTO memory_proposals (
                        proposal_id, user_id, memory_type, summary, scenario_type,
                        source_type, source_id, evidence_type, confidence,
                        occurred_at, status, policy_reason, content_hash,
                        idempotency_key, version, created_at, updated_at, expires_at
                        ) VALUES (
                        :proposal_id, :user_id, :memory_type, :summary,
                        :scenario_type, :source_type, :source_id, :evidence_type,
                        :confidence, :occurred_at, :status, :policy_reason,
                        :content_hash, :idempotency_key, :version, :created_at,
                        :updated_at, :expires_at
                        )"""
                    ),
                    _proposal_values(record),
                )
                _insert_event(
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
        except IntegrityError as error:
            raise MemoryConflictError("memory proposal already exists") from error
        return record

    def get_for_user(
        self,
        proposal_id: str,
        user_id: str,
    ) -> PendingMemoryProposalRecord | None:
        """Return one proposal only when its owner matches."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT * FROM memory_proposals
                    WHERE proposal_id = :proposal_id AND user_id = :user_id"""
                ),
                {"proposal_id": proposal_id, "user_id": user_id},
            ).mappings().first()
        return _proposal_from_mapping(row) if row else None

    def get_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> PendingMemoryProposalRecord | None:
        """Resolve a safely retried proposal write."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT * FROM memory_proposals
                    WHERE user_id = :user_id
                    AND idempotency_key = :idempotency_key"""
                ),
                {
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                },
            ).mappings().first()
        return _proposal_from_mapping(row) if row else None

    def list_pending(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PendingMemoryProposalRecord]:
        """Return bounded pending proposals for one user."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT * FROM memory_proposals
                    WHERE user_id = :user_id AND status = :status
                    ORDER BY created_at DESC
                    LIMIT :limit"""
                ),
                {
                    "user_id": user_id,
                    "status": MemoryProposalStatus.PENDING_CONFIRMATION.value,
                    "limit": min(max(limit, 1), 500),
                },
            ).mappings().all()
        return [_proposal_from_mapping(row) for row in rows]

    def consume_pending(
        self,
        *,
        user_id: str,
        proposal_id: str,
        expected_version: int,
        target_status: MemoryProposalStatus,
        reason_code: str,
        changed_at: datetime,
    ) -> None:
        """Delete decided proposal content and retain only a content-free event."""
        if target_status not in {
            MemoryProposalStatus.CONFIRMED,
            MemoryProposalStatus.REJECTED,
        }:
            raise ValueError("pending proposals may only be confirmed or rejected")
        with self.engine.begin() as connection:
            row = connection.execute(
                text(
                    """SELECT version, status FROM memory_proposals
                    WHERE proposal_id = :proposal_id AND user_id = :user_id
                    FOR UPDATE"""
                ),
                {"proposal_id": proposal_id, "user_id": user_id},
            ).mappings().first()
            if row is None:
                raise MemoryConflictError("pending memory proposal was not found")
            if (
                row["status"] != MemoryProposalStatus.PENDING_CONFIRMATION.value
                or row["version"] != expected_version
            ):
                raise MemoryConflictError(
                    "pending memory proposal was changed concurrently"
                )
            result = connection.execute(
                text(
                    """DELETE FROM memory_proposals
                    WHERE proposal_id = :proposal_id AND user_id = :user_id
                    AND version = :expected_version AND status = :status"""
                ),
                {
                    "proposal_id": proposal_id,
                    "user_id": user_id,
                    "expected_version": expected_version,
                    "status": MemoryProposalStatus.PENDING_CONFIRMATION.value,
                },
            )
            if result.rowcount != 1:
                raise MemoryConflictError(
                    "pending memory proposal was changed concurrently"
                )
            _insert_event(
                connection,
                MemoryEvent(
                    user_id=user_id,
                    subject_type=MemorySubjectType.MEMORY_PROPOSAL,
                    subject_id=proposal_id,
                    event_type=(
                        MemoryEventType.PROPOSAL_CONFIRMED
                        if target_status == MemoryProposalStatus.CONFIRMED
                        else MemoryEventType.PROPOSAL_REJECTED
                    ),
                    from_status=MemoryProposalStatus.PENDING_CONFIRMATION.value,
                    to_status=target_status.value,
                    reason_code=reason_code,
                    subject_version=expected_version + 1,
                    created_at=changed_at,
                ),
            )

    def record_rejection(
        self,
        *,
        user_id: str,
        proposal_id: str,
        reason_code: str,
        created_at: datetime,
    ) -> None:
        """Audit a rejection without persisting rejected candidate content."""
        with self.engine.begin() as connection:
            _insert_event(
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


def _proposal_values(record: PendingMemoryProposalRecord) -> dict[str, object]:
    return {
        "proposal_id": record.proposal_id,
        "user_id": record.user_id,
        "memory_type": record.memory_type.value,
        "summary": record.summary,
        "scenario_type": record.scenario_type.value if record.scenario_type else None,
        "source_type": record.source_type.value,
        "source_id": record.source_id,
        "evidence_type": record.evidence_type.value,
        "confidence": record.confidence,
        "occurred_at": record.occurred_at,
        "status": record.status.value,
        "policy_reason": record.policy_reason.value,
        "content_hash": record.content_hash,
        "idempotency_key": record.idempotency_key,
        "version": record.version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
    }


def _proposal_from_mapping(row: RowMapping) -> PendingMemoryProposalRecord:
    return PendingMemoryProposalRecord.model_validate(dict(row))


def _insert_event(connection: Connection, event: MemoryEvent) -> None:
    connection.execute(
        text(
            """INSERT INTO memory_events (
            event_id, user_id, subject_type, subject_id, event_type,
            from_status, to_status, reason_code, subject_version, created_at
            ) VALUES (
            :event_id, :user_id, :subject_type, :subject_id, :event_type,
            :from_status, :to_status, :reason_code, :subject_version, :created_at
            )"""
        ),
        {
            "event_id": event.event_id,
            "user_id": event.user_id,
            "subject_type": event.subject_type.value,
            "subject_id": event.subject_id,
            "event_type": event.event_type.value,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "reason_code": event.reason_code,
            "subject_version": event.subject_version,
            "created_at": event.created_at,
        },
    )
