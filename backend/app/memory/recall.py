"""Database-independent multi-route recall for episodic memory summaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.memory.hard_filter import MemoryHardFilter, MemoryHardFilterReport
from app.memory.text_semantics import lexical_terms
from app.models_long_term_memory import EpisodicMemoryRecord, MemoryRetrievalRequest


class DenseEmbeddingProvider(Protocol):
    """Embedding contract independent of an inference engine or vector store."""

    provider_name: str
    model_name: str
    model_revision: str
    dimensions: int
    model_size_mb: float

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class MemoryRecallChannel(str, Enum):
    """Auditable sources contributing candidates to the union."""

    DENSE = "dense"
    BM25 = "bm25"
    METADATA = "metadata"
    MULTI_QUERY = "multi_query"


class ExpandedMemoryQuery(BaseModel):
    """Bounded internal query variants; never suitable for trace serialization."""

    model_config = ConfigDict(extra="forbid")

    original: str = Field(min_length=1, max_length=1200)
    variants: list[str] = Field(min_length=1, max_length=4)


class RecallCandidateDiagnostic(BaseModel):
    """Content-free rank and score details for one candidate."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    rrf_score: float = Field(ge=0.0, le=1.0)
    channel_ranks: dict[MemoryRecallChannel, int] = Field(default_factory=dict)
    channel_scores: dict[MemoryRecallChannel, float] = Field(default_factory=dict)


class MultiRouteRecallDiagnostics(BaseModel):
    """Aggregate telemetry with no query or memory summary."""

    model_config = ConfigDict(extra="forbid")

    query_variant_count: int = Field(ge=1, le=4)
    filtered: MemoryHardFilterReport
    recalled_by_channel: dict[MemoryRecallChannel, int] = Field(default_factory=dict)
    union_count: int = Field(ge=0)
    candidates: list[RecallCandidateDiagnostic] = Field(default_factory=list)


@dataclass(frozen=True)
class RecalledMemory:
    """Internal candidate carrying content plus content-free recall evidence."""

    record: EpisodicMemoryRecord
    rrf_score: float
    channel_ranks: dict[MemoryRecallChannel, int] = field(default_factory=dict)
    channel_scores: dict[MemoryRecallChannel, float] = field(default_factory=dict)


@dataclass(frozen=True)
class MultiRouteRecallResult:
    """Internal candidate union and safe diagnostics."""

    candidates: list[RecalledMemory]
    diagnostics: MultiRouteRecallDiagnostics


class DeterministicMemoryQueryExpander:
    """Generate conservative variants without sending private text to an LLM."""

    _SYNONYMS = {
        "发言": "表达 观点 开场",
        "开场": "开始表达 第一句",
        "拒绝": "边界 不能承担 替代选择",
        "边界": "拒绝 请求 可承担范围",
        "紧张": "停顿 节奏 准备",
        "提问": "询问 具体问题",
        "冲突": "分歧 沟通 具体请求",
        "汇报": "展示 说明 核心观点",
    }

    def expand(self, request: MemoryRetrievalRequest) -> ExpandedMemoryQuery:
        """Keep the original in every semantic expansion to preserve constraints."""
        variants = [request.query]
        matched = [
            expansion
            for term, expansion in self._SYNONYMS.items()
            if term in request.query
        ]
        if matched:
            variants.append(f"{request.query} {' '.join(matched[:2])}")
        scope_terms = [
            value
            for value in (
                request.scenario_type,
                request.practice_thread_id,
                *(skill.value for skill in request.skill_codes),
            )
            if value
        ]
        if scope_terms:
            variants.append(f"{request.query} {' '.join(scope_terms[:4])}")
        normalized = " ".join(sorted(lexical_terms(request.query)))
        if normalized and normalized != request.query:
            variants.append(f"{request.query} {normalized}")
        return ExpandedMemoryQuery(
            original=request.query,
            variants=list(dict.fromkeys(variants))[:4],
        )


class MultiRouteMemoryRecall:
    """Recall through dense, BM25, metadata and expanded-query routes."""

    def __init__(
        self,
        *,
        embedder: DenseEmbeddingProvider,
        hard_filter: MemoryHardFilter | None = None,
        query_expander: DeterministicMemoryQueryExpander | None = None,
        per_channel_limit: int = 20,
        dense_min_score: float = 0.0,
        rrf_k: int = 60,
    ) -> None:
        self.embedder = embedder
        self.hard_filter = hard_filter or MemoryHardFilter()
        self.query_expander = query_expander or DeterministicMemoryQueryExpander()
        self.per_channel_limit = min(max(per_channel_limit, 1), 50)
        self.dense_min_score = min(max(dense_min_score, -1.0), 1.0)
        self.rrf_k = min(max(rrf_k, 1), 100)

    def recall(
        self,
        *,
        request: MemoryRetrievalRequest,
        records: list[EpisodicMemoryRecord],
        now,
    ) -> MultiRouteRecallResult:
        """Filter first, recall independently, then fuse ranks by memory id."""
        eligible, filter_report = self.hard_filter.filter(
            records=records,
            request=request,
            now=now,
        )
        expanded = self.query_expander.expand(request)
        if not eligible:
            return MultiRouteRecallResult(
                candidates=[],
                diagnostics=MultiRouteRecallDiagnostics(
                    query_variant_count=len(expanded.variants),
                    filtered=filter_report,
                    union_count=0,
                ),
            )

        route_results = {
            MemoryRecallChannel.DENSE: self._dense(
                query=expanded.original,
                records=eligible,
            ),
            MemoryRecallChannel.BM25: self._bm25(
                query=expanded.original,
                records=eligible,
            ),
            MemoryRecallChannel.METADATA: self._metadata(
                request=request,
                records=eligible,
                now=now,
            ),
            MemoryRecallChannel.MULTI_QUERY: self._expanded(
                queries=expanded.variants[1:],
                records=eligible,
            ),
        }
        candidates = self._fuse(route_results, eligible)
        diagnostics = MultiRouteRecallDiagnostics(
            query_variant_count=len(expanded.variants),
            filtered=filter_report,
            recalled_by_channel={
                channel: len(items) for channel, items in route_results.items()
            },
            union_count=len(candidates),
            candidates=[
                RecallCandidateDiagnostic(
                    memory_id=item.record.memory_id,
                    rrf_score=item.rrf_score,
                    channel_ranks=item.channel_ranks,
                    channel_scores=item.channel_scores,
                )
                for item in candidates
            ],
        )
        return MultiRouteRecallResult(
            candidates=candidates,
            diagnostics=diagnostics,
        )

    def _dense(
        self,
        *,
        query: str,
        records: list[EpisodicMemoryRecord],
    ) -> list[tuple[str, float]]:
        query_vector = self.embedder.embed_queries([query])[0]
        vectors = self.embedder.embed_documents(
            [record.summary for record in records]
        )
        if len(vectors) != len(records):
            raise RuntimeError("Embedding provider returned an incomplete batch")
        scored = [
            (record.memory_id, _cosine(query_vector, vector))
            for record, vector in zip(records, vectors, strict=True)
        ]
        return _top(scored, self.per_channel_limit, self.dense_min_score)

    def _bm25(
        self,
        *,
        query: str,
        records: list[EpisodicMemoryRecord],
    ) -> list[tuple[str, float]]:
        query_terms = lexical_terms(query)
        documents = [list(lexical_terms(record.summary)) for record in records]
        if not query_terms or not any(documents):
            return []
        document_frequency = Counter(
            term for document in documents for term in set(document)
        )
        average_length = sum(map(len, documents)) / len(documents)
        scored = []
        for record, document in zip(records, documents, strict=True):
            frequencies = Counter(document)
            score = 0.0
            for term in query_terms:
                frequency = frequencies[term]
                if frequency == 0:
                    continue
                inverse_document_frequency = math.log(
                    1.0
                    + (len(documents) - document_frequency[term] + 0.5)
                    / (document_frequency[term] + 0.5)
                )
                denominator = frequency + 1.2 * (
                    1.0 - 0.75 + 0.75 * len(document) / max(average_length, 1.0)
                )
                score += inverse_document_frequency * frequency * 2.2 / denominator
            scored.append((record.memory_id, score))
        return _normalize_positive(_top(scored, self.per_channel_limit, 0.0))

    def _metadata(
        self,
        *,
        request: MemoryRetrievalRequest,
        records: list[EpisodicMemoryRecord],
        now,
    ) -> list[tuple[str, float]]:
        scored: list[tuple[str, float]] = []
        requested_skills = set(request.skill_codes)
        for record in records:
            continuity = (
                1.0
                if request.practice_thread_id
                and record.practice_thread_id == request.practice_thread_id
                else 0.8
                if request.scenario_id and record.scenario_id == request.scenario_id
                else 0.0
            )
            skill = (
                len(requested_skills.intersection(record.skill_codes))
                / len(requested_skills)
                if requested_skills
                else 0.0
            )
            scenario = (
                1.0
                if request.scenario_type
                and record.scenario_type == request.scenario_type
                else 0.0
            )
            if continuity == 0.0 and skill == 0.0 and scenario == 0.0:
                continue
            age_days = max(0.0, (now - record.occurred_at).total_seconds() / 86400)
            recency = max(0.0, 1.0 - age_days / 365.0)
            score = (
                continuity * 0.45
                + skill * 0.25
                + scenario * 0.15
                + recency * 0.10
                + record.confidence * 0.05
            )
            scored.append((record.memory_id, score))
        return _top(scored, self.per_channel_limit, 0.0)

    def _expanded(
        self,
        *,
        queries: list[str],
        records: list[EpisodicMemoryRecord],
    ) -> list[tuple[str, float]]:
        best: dict[str, float] = {}
        for query in queries:
            for memory_id, score in (
                *self._dense(query=query, records=records),
                *self._bm25(query=query, records=records),
            ):
                best[memory_id] = max(best.get(memory_id, -1.0), score)
        return _top(list(best.items()), self.per_channel_limit, 0.0)

    def _fuse(
        self,
        route_results: dict[MemoryRecallChannel, list[tuple[str, float]]],
        records: list[EpisodicMemoryRecord],
    ) -> list[RecalledMemory]:
        record_by_id = {record.memory_id: record for record in records}
        ranks: dict[str, dict[MemoryRecallChannel, int]] = defaultdict(dict)
        scores: dict[str, dict[MemoryRecallChannel, float]] = defaultdict(dict)
        rrf: Counter[str] = Counter()
        active_channel_count = sum(bool(items) for items in route_results.values())
        for channel, items in route_results.items():
            for rank, (memory_id, score) in enumerate(items, start=1):
                ranks[memory_id][channel] = rank
                scores[memory_id][channel] = round(score, 6)
                rrf[memory_id] += 1.0 / (self.rrf_k + rank)
        maximum = active_channel_count / (self.rrf_k + 1)
        fused = [
            RecalledMemory(
                record=record_by_id[memory_id],
                rrf_score=round(value / maximum, 6) if maximum else 0.0,
                channel_ranks=ranks[memory_id],
                channel_scores=scores[memory_id],
            )
            for memory_id, value in rrf.items()
        ]
        return sorted(
            fused,
            key=lambda item: (
                item.rrf_score,
                item.record.occurred_at,
                item.record.memory_id,
            ),
            reverse=True,
        )


def _top(
    scores: list[tuple[str, float]],
    limit: int,
    minimum: float,
) -> list[tuple[str, float]]:
    return sorted(
        (item for item in scores if item[1] > minimum),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )[:limit]


def _normalize_positive(
    scores: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    maximum = max((score for _, score in scores), default=0.0)
    if maximum <= 0.0:
        return []
    return [(memory_id, score / maximum) for memory_id, score in scores]


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise RuntimeError("Embedding provider returned a zero vector")
    return max(
        -1.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm),
        ),
    )
