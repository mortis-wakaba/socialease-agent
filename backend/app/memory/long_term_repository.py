"""Database-independent contract and policies for durable Agent Memory."""

from datetime import datetime, timezone
from typing import Protocol

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
    """Persistence contract for episodic memory and thread checkpoints."""

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

    async def search_memory_fts_candidates(
        self,
        *,
        user_id: str,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
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
