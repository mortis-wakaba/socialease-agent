"""Privacy and scoring contracts for Cross-Encoder memory reranking."""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.memory.recall import MemoryRecallChannel, RecalledMemory
from app.memory.reranker import CrossEncoderMemoryReranker, MemoryRerankWeights
from app.evals.cross_encoder import FastEmbedBgeReranker
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryRetrievalRequest,
    MemorySourceType,
    MemoryType,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


class _SpyCrossEncoder:
    provider_name = "local_test"
    model_name = "spy"
    model_revision = "1"

    def __init__(self) -> None:
        self.seen_query = ""
        self.seen_documents: list[str] = []

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        self.seen_query = query
        self.seen_documents = list(documents)
        return [4.0 if "核心观点" in document else -2.0 for document in documents]


def _record(memory_id: str, summary: str, *, user_id: str = "user_a"):
    digest = hashlib.sha256(summary.encode()).hexdigest()
    return EpisodicMemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
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


def _recalled(record: EpisodicMemoryRecord, score: float = 0.5) -> RecalledMemory:
    return RecalledMemory(
        record=record,
        rrf_score=score,
        channel_ranks={
            MemoryRecallChannel.DENSE: 1,
            MemoryRecallChannel.BM25: 1,
        },
        channel_scores={
            MemoryRecallChannel.DENSE: score,
            MemoryRecallChannel.BM25: score,
        },
    )


def _request() -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        user_id="user_a",
        query="发言时怎样更清楚地说明想法？",
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
    )


def test_cross_encoder_only_receives_safe_bounded_summaries() -> None:
    provider = _SpyCrossEncoder()
    candidates = [
        _recalled(_record("target", "先说一句核心观点，再补充理由。")),
        _recalled(_record("pii", "联系 13912345678 后再练习。")),
        *[
            _recalled(_record(f"extra_{index}", f"普通练习策略 {index}。"))
            for index in range(25)
        ],
    ]

    result = CrossEncoderMemoryReranker(provider=provider).rerank(
        request=_request(),
        candidates=candidates,
        now=NOW,
    )

    assert len(provider.seen_documents) == 20
    assert not any("13912345678" in item for item in provider.seen_documents)
    assert result.candidates[0].recalled.record.memory_id == "target"


def test_rerank_diagnostics_never_copy_private_text() -> None:
    provider = _SpyCrossEncoder()
    request = _request()
    record = _record("target", "先说一句核心观点，再补充理由。")

    result = CrossEncoderMemoryReranker(provider=provider).rerank(
        request=request,
        candidates=[_recalled(record)],
        now=NOW,
    )
    serialized = result.diagnostics.model_dump_json()

    assert request.query not in serialized
    assert record.summary not in serialized
    assert result.diagnostics.provider_name == "local_test"


def test_rerank_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        MemoryRerankWeights(cross_encoder=0.50)


def test_invalid_cross_encoder_output_fails_closed() -> None:
    class _Invalid(_SpyCrossEncoder):
        def score(self, query: str, documents: Sequence[str]) -> list[float]:
            return [float("nan")] * len(documents)

    with pytest.raises(RuntimeError, match="non-finite"):
        CrossEncoderMemoryReranker(provider=_Invalid()).rerank(
            request=_request(),
            candidates=[_recalled(_record("target", "核心观点"))],
            now=NOW,
        )


def test_local_cross_encoder_path_fails_before_model_load_when_incomplete(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.json").touch()
    with pytest.raises(RuntimeError, match="onnx/model.onnx"):
        FastEmbedBgeReranker(specific_model_path=str(tmp_path))
