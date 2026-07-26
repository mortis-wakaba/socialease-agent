"""Shared idempotent commit path for approved episodic memories."""

from datetime import datetime

from app.memory.identity import (
    MEMORY_CONSENT_VERSION,
    memory_content_hash,
    memory_expiry,
    memory_idempotency_key,
)
from app.memory.long_term_repository import (
    LongTermMemoryRepository,
    MemoryConflictError,
)
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryProposal,
    MemoryRecordStatus,
)


class EpisodicMemoryCommitter:
    """Persist policy-approved memory with one race-safe identity contract."""

    def __init__(self, repository: LongTermMemoryRepository) -> None:
        self.repository = repository

    def commit(
        self,
        *,
        user_id: str,
        proposal: MemoryProposal,
        safe_summary: str,
        reason_code: str,
        timestamp: datetime,
        idempotency_key: str | None = None,
        evidence_type: MemoryEvidenceType | None = None,
        confidence: float | None = None,
    ) -> tuple[EpisodicMemoryRecord, bool]:
        """Create once, or return the winner of a concurrent equivalent write."""
        key = idempotency_key or memory_idempotency_key(
            user_id=user_id,
            source_type=proposal.source_type.value,
            memory_type=proposal.memory_type.value,
            summary=safe_summary,
        )
        existing = self.repository.get_memory_by_idempotency_key(
            user_id=user_id,
            idempotency_key=key,
        )
        if existing is not None:
            return existing, True
        record = EpisodicMemoryRecord(
            memory_id=f"memory_{key[:32]}",
            user_id=user_id,
            memory_type=proposal.memory_type,
            summary=safe_summary,
            scenario_type=proposal.scenario_type,
            source_type=proposal.source_type,
            source_id=proposal.source_id,
            evidence_type=evidence_type or proposal.evidence_type,
            confidence=(
                proposal.confidence if confidence is None else confidence
            ),
            status=MemoryRecordStatus.ACTIVE,
            occurred_at=proposal.occurred_at,
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=memory_expiry(
                memory_type=proposal.memory_type,
                created_at=timestamp,
            ),
            consent_version=MEMORY_CONSENT_VERSION,
            content_hash=memory_content_hash(safe_summary),
            idempotency_key=key,
        )
        try:
            return (
                self.repository.create_memory(record, reason_code=reason_code),
                False,
            )
        except MemoryConflictError:
            existing = self.repository.get_memory_by_idempotency_key(
                user_id=user_id,
                idempotency_key=key,
            )
            if existing is None:
                raise
            return existing, True
