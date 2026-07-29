"""Private, bounded Cross-Encoder reranking for recalled episodic memories."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.memory.hard_filter import MemoryHardFilter, MemoryHardFilterReason
from app.memory.recall import MemoryRecallChannel, RecalledMemory
from app.models_long_term_memory import MemoryRetrievalRequest


class CrossEncoderProvider(Protocol):
    """Local or trusted inference contract for query-summary pair scoring."""

    provider_name: str
    model_name: str
    model_revision: str

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class MemoryRerankWeights(BaseModel):
    """Validated score fusion weights shared by runtime and evaluations."""

    model_config = ConfigDict(extra="forbid")

    cross_encoder: float = Field(default=0.60, ge=0.0, le=1.0)
    rrf: float = Field(default=0.20, ge=0.0, le=1.0)
    dense: float = Field(default=0.08, ge=0.0, le=1.0)
    bm25: float = Field(default=0.06, ge=0.0, le=1.0)
    metadata: float = Field(default=0.06, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_unit_sum(self) -> "MemoryRerankWeights":
        """Reject accidental changes that silently alter score calibration."""
        total = (
            self.cross_encoder
            + self.rrf
            + self.dense
            + self.bm25
            + self.metadata
        )
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError("memory rerank weights must sum to 1")
        return self


class RerankedCandidateDiagnostic(BaseModel):
    """Content-free final score components for one candidate."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    final_score: float = Field(ge=0.0, le=1.0)
    cross_encoder_score: float = Field(ge=0.0, le=1.0)
    rrf_score: float = Field(ge=0.0, le=1.0)
    dense_score: float = Field(ge=0.0, le=1.0)
    bm25_score: float = Field(ge=0.0, le=1.0)
    metadata_score: float = Field(ge=0.0, le=1.0)


class MemoryRerankDiagnostics(BaseModel):
    """Provider metadata and scores without private input text."""

    model_config = ConfigDict(extra="forbid")

    provider_name: str
    model_name: str
    model_revision: str
    input_candidate_count: int = Field(ge=0)
    reranked_candidate_count: int = Field(ge=0, le=20)
    candidates: list[RerankedCandidateDiagnostic] = Field(default_factory=list)


@dataclass(frozen=True)
class RerankedMemory:
    """Internal candidate after pairwise scoring and calibrated fusion."""

    recalled: RecalledMemory
    final_score: float
    cross_encoder_score: float


@dataclass(frozen=True)
class MemoryRerankResult:
    """Internal reranked candidates and safe diagnostics."""

    candidates: list[RerankedMemory]
    diagnostics: MemoryRerankDiagnostics


class CrossEncoderMemoryReranker:
    """Rerank at most 20 already-filtered summaries using local pair scoring."""

    def __init__(
        self,
        *,
        provider: CrossEncoderProvider,
        hard_filter: MemoryHardFilter | None = None,
        weights: MemoryRerankWeights | None = None,
        candidate_limit: int = 20,
    ) -> None:
        self.provider = provider
        self.hard_filter = hard_filter or MemoryHardFilter()
        self.weights = weights or MemoryRerankWeights()
        self.candidate_limit = min(max(candidate_limit, 1), 20)

    def rerank(
        self,
        *,
        request: MemoryRetrievalRequest,
        candidates: list[RecalledMemory],
        now,
    ) -> MemoryRerankResult:
        """Recheck safety, score bounded pairs and return calibrated ordering."""
        safe = [
            item
            for item in candidates
            if self.hard_filter.evaluate(
                record=item.record,
                request=request,
                now=now,
            )
            == MemoryHardFilterReason.ALLOWED
        ][: self.candidate_limit]
        if not safe:
            return self._result(input_count=len(candidates), candidates=[])

        raw_scores = self.provider.score(
            request.query,
            [item.record.summary for item in safe],
        )
        if len(raw_scores) != len(safe):
            raise RuntimeError("Cross-Encoder returned an incomplete score batch")
        if any(not math.isfinite(score) for score in raw_scores):
            raise RuntimeError("Cross-Encoder returned a non-finite score")

        reranked = [
            self._combine(item, _sigmoid(raw_score))
            for item, raw_score in zip(safe, raw_scores, strict=True)
        ]
        reranked.sort(
            key=lambda item: (
                item.final_score,
                item.recalled.record.occurred_at,
                item.recalled.record.memory_id,
            ),
            reverse=True,
        )
        return self._result(input_count=len(candidates), candidates=reranked)

    def _combine(
        self,
        item: RecalledMemory,
        cross_encoder_score: float,
    ) -> RerankedMemory:
        dense = _bounded_channel_score(item, MemoryRecallChannel.DENSE)
        bm25 = _bounded_channel_score(item, MemoryRecallChannel.BM25)
        metadata = _bounded_channel_score(item, MemoryRecallChannel.METADATA)
        final = (
            cross_encoder_score * self.weights.cross_encoder
            + item.rrf_score * self.weights.rrf
            + dense * self.weights.dense
            + bm25 * self.weights.bm25
            + metadata * self.weights.metadata
        )
        return RerankedMemory(
            recalled=item,
            final_score=round(min(max(final, 0.0), 1.0), 6),
            cross_encoder_score=round(cross_encoder_score, 6),
        )

    def _result(
        self,
        *,
        input_count: int,
        candidates: list[RerankedMemory],
    ) -> MemoryRerankResult:
        return MemoryRerankResult(
            candidates=candidates,
            diagnostics=MemoryRerankDiagnostics(
                provider_name=self.provider.provider_name,
                model_name=self.provider.model_name,
                model_revision=self.provider.model_revision,
                input_candidate_count=input_count,
                reranked_candidate_count=len(candidates),
                candidates=[
                    RerankedCandidateDiagnostic(
                        memory_id=item.recalled.record.memory_id,
                        final_score=item.final_score,
                        cross_encoder_score=item.cross_encoder_score,
                        rrf_score=item.recalled.rrf_score,
                        dense_score=_bounded_channel_score(
                            item.recalled,
                            MemoryRecallChannel.DENSE,
                        ),
                        bm25_score=_bounded_channel_score(
                            item.recalled,
                            MemoryRecallChannel.BM25,
                        ),
                        metadata_score=_bounded_channel_score(
                            item.recalled,
                            MemoryRecallChannel.METADATA,
                        ),
                    )
                    for item in candidates
                ],
            ),
        )


def _bounded_channel_score(
    item: RecalledMemory,
    channel: MemoryRecallChannel,
) -> float:
    score = item.channel_scores.get(channel, 0.0)
    if channel == MemoryRecallChannel.DENSE:
        score = (score + 1.0) / 2.0
    return round(min(max(score, 0.0), 1.0), 6)


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)
