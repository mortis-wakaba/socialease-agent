"""Contracts shared by every durable-memory write and semantic consumer."""

from datetime import datetime, timedelta, timezone

import pytest

from app.memory.commit_service import EpisodicMemoryCommitter
from app.memory.identity import (
    memory_content_hash,
    memory_expiry,
    memory_idempotency_key,
)
from app.memory.long_term_repository import MemoryConflictError
from app.memory.text_semantics import conflict_overlap, memories_conflict
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryProposal,
    MemorySourceType,
    MemoryType,
)


NOW = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)


class RacingMemoryRepository:
    """Simulate another writer winning after the initial idempotency read."""

    def __init__(self) -> None:
        self.record: EpisodicMemoryRecord | None = None
        self.create_calls = 0

    async def get_memory_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> EpisodicMemoryRecord | None:
        if (
            self.record is not None
            and self.record.user_id == user_id
            and self.record.idempotency_key == idempotency_key
        ):
            return self.record
        return None

    async def create_memory(
        self,
        record: EpisodicMemoryRecord,
        *,
        reason_code: str,
    ) -> EpisodicMemoryRecord:
        assert reason_code == "policy_auto_commit"
        self.create_calls += 1
        self.record = record
        raise MemoryConflictError("simulated concurrent winner")


def test_identity_normalization_and_retention_are_canonical() -> None:
    compact = "先 写 一句 开场"
    spaced = "  先  写 一句   开场  "

    assert memory_content_hash(compact) == memory_content_hash(spaced)
    assert memory_idempotency_key(
        user_id="owner",
        source_type="chat",
        memory_type="helpful_strategy",
        summary=compact,
    ) == memory_idempotency_key(
        user_id="owner",
        source_type="chat",
        memory_type="helpful_strategy",
        summary=spaced,
    )
    assert memory_expiry(
        memory_type=MemoryType.HELPFUL_STRATEGY,
        created_at=NOW,
    ) == NOW + timedelta(days=730)
    assert memory_expiry(
        memory_type=MemoryType.PRACTICE_EXPERIENCE,
        created_at=NOW,
    ) == NOW + timedelta(days=365)


def test_shared_conflict_semantics_exposes_overlap_and_decision() -> None:
    helpful = "小组讨论前先写一句开场，再表达观点"
    rejected = "小组讨论前不要写开场，也不要表达观点"

    assert conflict_overlap(helpful, rejected) >= 2
    assert memories_conflict(helpful, rejected)
    assert not memories_conflict(helpful, "课堂发言前先深呼吸")


@pytest.mark.anyio
async def test_committer_recovers_the_concurrent_idempotent_winner() -> None:
    repository = RacingMemoryRepository()
    proposal = MemoryProposal(
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary="先写一句开场对表达观点有帮助。",
        scenario_type="group_discussion",
        source_type=MemorySourceType.CHAT,
        source_id="request-1",
        evidence_type=MemoryEvidenceType.EXPLICIT_USER_STATEMENT,
        confidence=0.9,
        occurred_at=NOW,
    )

    record, deduplicated = await EpisodicMemoryCommitter(repository).commit(
        user_id="owner",
        proposal=proposal,
        safe_summary=proposal.summary,
        reason_code="policy_auto_commit",
        timestamp=NOW,
    )

    assert repository.create_calls == 1
    assert record is repository.record
    assert deduplicated is True
    assert record.expires_at == NOW + timedelta(days=730)
