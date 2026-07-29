"""Tests for explicit long-term-memory abstention decisions."""

from datetime import datetime, timedelta, timezone
import hashlib

from app.memory.abstention import MemoryAbstentionPolicy, MemoryAbstentionReason
from app.memory.recall import RecalledMemory
from app.memory.reranker import RerankedMemory
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryRetrievalRequest,
    MemorySourceType,
    MemoryType,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _record(memory_id: str, summary: str) -> EpisodicMemoryRecord:
    digest = hashlib.sha256(summary.encode()).hexdigest()
    return EpisodicMemoryRecord(
        memory_id=memory_id,
        user_id="user_a",
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary=summary,
        source_type=MemorySourceType.USER_CONFIRMED,
        source_id="demo_source",
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=0.95,
        occurred_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        consent_version="demo-v1",
        content_hash=digest,
        idempotency_key=digest,
    )


def _candidate(memory_id: str, summary: str, score: float) -> RerankedMemory:
    return RerankedMemory(
        recalled=RecalledMemory(
            record=_record(memory_id, summary),
            rrf_score=score,
        ),
        final_score=score,
        cross_encoder_score=score,
    )


def _request(query: str = "课堂发言时如何先说一句观点？"):
    return MemoryRetrievalRequest(
        user_id="user_a",
        query=query,
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
    )


def test_abstains_when_no_candidate_or_top_score_is_low() -> None:
    policy = MemoryAbstentionPolicy(minimum_score=0.45)

    empty = policy.decide(request=_request(), candidates=[], now=NOW)
    low = policy.decide(
        request=_request(),
        candidates=[_candidate("low", "先说观点。", 0.44)],
        now=NOW,
    )

    assert empty.diagnostics.reason == MemoryAbstentionReason.NO_CANDIDATES
    assert low.diagnostics.reason == MemoryAbstentionReason.LOW_TOP_SCORE


def test_abstains_on_near_tied_conflicting_memories() -> None:
    decision = MemoryAbstentionPolicy(minimum_conflict_margin=0.03).decide(
        request=_request("我现在想决定课堂发言时是否提前背完整稿子。"),
        candidates=[
            _candidate("yes", "提前背完整稿子对我有帮助。", 0.80),
            _candidate("no", "提前背完整稿子对我没有帮助。", 0.78),
        ],
        now=NOW,
    )

    assert decision.selected == []
    assert decision.diagnostics.reason == MemoryAbstentionReason.AMBIGUOUS_CONFLICT


def test_selects_high_confidence_non_conflicting_candidates() -> None:
    decision = MemoryAbstentionPolicy().decide(
        request=_request(),
        candidates=[
            _candidate("top", "课堂发言先说一句核心观点。", 0.82),
            _candidate("second", "发言前写下两个关键词。", 0.64),
        ],
        now=NOW,
    )

    assert [item.recalled.record.memory_id for item in decision.selected] == [
        "top",
        "second",
    ]
    assert decision.diagnostics.reason == MemoryAbstentionReason.NONE


def test_reranker_failure_has_explicit_reason_and_no_fallback_hits() -> None:
    decision = MemoryAbstentionPolicy().reranker_unavailable(candidate_count=12)

    assert decision.selected == []
    assert decision.diagnostics.reason == MemoryAbstentionReason.RERANKER_UNAVAILABLE
