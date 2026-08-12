"""Consent-gated, explainable episodic-memory retrieval baselines."""

import asyncio
from datetime import datetime, timezone
import logging
import os

from app.memory.hard_filter import MemoryHardFilter, MemoryHardFilterReason
from app.memory.embedding import (
    create_memory_embedding_provider,
    memory_retrieval_mode,
    run_embedding,
)
from app.memory.long_term_repository import LongTermMemoryRepository
from app.memory.recall import DenseEmbeddingProvider
from app.memory.settings_store import UserMemorySettingsRepository
from app.memory.text_semantics import fts_query_terms, lexical_terms
from app.memory.token_estimator import ConservativeTokenEstimator, TokenEstimator
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryRecordStatus,
    MemoryRetrievalDiagnostics,
    MemoryRetrievalHit,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    MemoryRetrievalScore,
    MemoryRetrievalStrategy,
    MemoryType,
)
logger = logging.getLogger(__name__)
_HARD_FILTER = MemoryHardFilter()


class EpisodicMemoryRetriever:
    """Retrieve a few safe memories while repositories enforce hard scope."""

    def __init__(
        self,
        *,
        repository: LongTermMemoryRepository,
        settings_repository: UserMemorySettingsRepository,
        token_estimator: TokenEstimator | None = None,
        context_token_budget: int = 384,
        candidate_limit: int = 100,
        retrieval_mode: str | None = None,
        embedding_provider: DenseEmbeddingProvider | None = None,
        vector_minimum_similarity: float | None = None,
        hybrid_rrf_k: int = 60,
    ) -> None:
        self.repository = repository
        self.settings_repository = settings_repository
        self.token_estimator = token_estimator or ConservativeTokenEstimator()
        self.context_token_budget = min(max(context_token_budget, 128), 1024)
        self.candidate_limit = min(max(candidate_limit, 10), 100)
        self.retrieval_mode = retrieval_mode or memory_retrieval_mode()
        if self.retrieval_mode not in {
            "sql_text",
            "postgres_fts",
            "dense_only",
            "hybrid",
        }:
            raise ValueError("unsupported episodic-memory retrieval mode")
        self.embedding_provider = (
            embedding_provider
            if embedding_provider is not None
            else create_memory_embedding_provider()
        )
        configured_similarity = (
            vector_minimum_similarity
            if vector_minimum_similarity is not None
            else float(
                os.getenv("SOCIALEASE_MEMORY_VECTOR_MIN_SIMILARITY", "0.45")
            )
        )
        self.vector_minimum_similarity = min(max(configured_similarity, -1.0), 1.0)
        self.hybrid_rrf_k = min(max(hybrid_rrf_k, 1), 100)

    async def retrieve(
        self,
        request: MemoryRetrievalRequest,
        *,
        now: datetime | None = None,
        record_usage: bool = True,
    ) -> MemoryRetrievalResult:
        """Return bounded hits; provider or model output never controls filters."""
        timestamp = _as_utc(now or datetime.now(timezone.utc))
        settings = await self.settings_repository.get(request.user_id)
        consent = settings.consent_state
        if not consent.consent_to_practice_summary:
            return _empty_result(
                request,
                token_budget=self.context_token_budget,
                consent_allowed=False,
            )
        disabled_types = {
            memory_type.value for memory_type in settings.disabled_memory_types
        }
        allowed_memory_types = tuple(
            memory_type
            for memory_type in dict.fromkeys(request.allowed_memory_types)
            if memory_type.value not in disabled_types
        )
        if not allowed_memory_types:
            return _empty_result(
                request,
                token_budget=self.context_token_budget,
                consent_allowed=True,
            )
        statuses = (MemoryRecordStatus.ACTIVE,)
        if request.include_archived:
            statuses = (*statuses, MemoryRecordStatus.ARCHIVED)
        candidates, fusion_scores, effective_strategy = await self._candidates(
            request=request,
            statuses=statuses,
            memory_types=allowed_memory_types,
            now=timestamp,
        )
        effective_request = request.model_copy(
            update={"strategy": effective_strategy}
        )
        hits, eligible_count = rank_memory_candidates(
            request=effective_request,
            candidates=candidates,
            now=timestamp,
            token_estimator=self.token_estimator,
            token_budget=self.context_token_budget,
            fusion_scores=fusion_scores,
        )
        audit_failed = False
        if hits and record_usage:
            try:
                await self.repository.record_retrieval(
                    user_id=request.user_id,
                    memory_ids=tuple(hit.memory_id for hit in hits),
                    retrieved_at=timestamp,
                    reason_code="context_retrieval",
                )
            except Exception as error:
                audit_failed = True
                logger.warning(
                    "Memory retrieval audit failed: %s",
                    error.__class__.__name__,
                )
        estimated_tokens = sum(hit.estimated_tokens for hit in hits)
        return MemoryRetrievalResult(
            hits=hits,
            diagnostics=MemoryRetrievalDiagnostics(
                strategy=effective_strategy,
                candidate_count=len(candidates),
                eligible_count=eligible_count,
                returned_count=len(hits),
                estimated_tokens=estimated_tokens,
                token_budget=self.context_token_budget,
                abstained=not hits,
                consent_allowed=True,
                audit_failed=audit_failed,
            ),
        )

    async def _candidates(
        self,
        *,
        request: MemoryRetrievalRequest,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        now: datetime,
    ) -> tuple[
        list[EpisodicMemoryRecord],
        dict[str, float],
        MemoryRetrievalStrategy,
    ]:
        """Use indexed recall when configured and fail back to the recent window."""
        if self.retrieval_mode == "sql_text":
            return await self._recent_candidates(
                request=request,
                statuses=statuses,
                memory_types=memory_types,
                now=now,
            )
        recall_query = request.query
        query_terms = tuple(fts_query_terms(recall_query))
        try:
            if self.retrieval_mode == "postgres_fts":
                fts = await self.repository.search_memory_fts_candidates(
                    user_id=request.user_id,
                    statuses=statuses,
                    memory_types=memory_types,
                    query_terms=query_terms,
                    now=now,
                    limit=self.candidate_limit,
                )
                return fts, {}, MemoryRetrievalStrategy.POSTGRES_FTS
            if self.retrieval_mode == "dense_only":
                provider = self.embedding_provider
                try:
                    if provider is None:
                        raise RuntimeError(
                            "dense-only memory retrieval requires an embedder"
                        )
                    vector = await self._vector_candidates(
                        request=request,
                        recall_query=recall_query,
                        statuses=statuses,
                        memory_types=memory_types,
                        now=now,
                        provider=provider,
                    )
                except Exception as error:
                    logger.warning(
                        "Dense memory recall failed; using PostgreSQL FTS: %s",
                        error.__class__.__name__,
                    )
                    fts = await self.repository.search_memory_fts_candidates(
                        user_id=request.user_id,
                        statuses=statuses,
                        memory_types=memory_types,
                        query_terms=query_terms,
                        now=now,
                        limit=self.candidate_limit,
                    )
                    return fts, {}, MemoryRetrievalStrategy.POSTGRES_FTS
                records, scores = _rrf_candidates(
                    fts=[],
                    vector=vector,
                    rrf_k=self.hybrid_rrf_k,
                    limit=self.candidate_limit,
                )
                return records, scores, MemoryRetrievalStrategy.DENSE_ONLY
            return await self._hybrid_candidates(
                request=request,
                recall_query=recall_query,
                statuses=statuses,
                memory_types=memory_types,
                query_terms=query_terms,
                now=now,
            )
        except Exception as error:
            logger.warning(
                "Indexed memory recall failed; using recent-window fallback: %s",
                error.__class__.__name__,
            )
            return await self._recent_candidates(
                request=request,
                statuses=statuses,
                memory_types=memory_types,
                now=now,
            )

    async def _hybrid_candidates(
        self,
        *,
        request: MemoryRetrievalRequest,
        recall_query: str,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        query_terms: tuple[str, ...],
        now: datetime,
    ) -> tuple[list[EpisodicMemoryRecord], dict[str, float], MemoryRetrievalStrategy]:
        provider = self.embedding_provider
        if provider is None:
            raise RuntimeError("hybrid memory retrieval requires an embedder")
        fts_task = asyncio.create_task(
            self.repository.search_memory_fts_candidates(
                user_id=request.user_id,
                statuses=statuses,
                memory_types=memory_types,
                query_terms=query_terms,
                now=now,
                limit=self.candidate_limit,
            )
        )
        vector_task = asyncio.create_task(
            self._vector_candidates(
                request=request,
                recall_query=recall_query,
                statuses=statuses,
                memory_types=memory_types,
                now=now,
                provider=provider,
            )
        )
        fts_result, vector_result = await asyncio.gather(
            fts_task,
            vector_task,
            return_exceptions=True,
        )
        if isinstance(fts_result, BaseException) and isinstance(
            vector_result, BaseException
        ):
            raise RuntimeError("both indexed memory routes failed") from vector_result
        if isinstance(vector_result, BaseException):
            logger.warning(
                "Vector memory recall failed; using PostgreSQL FTS: %s",
                vector_result.__class__.__name__,
            )
            if isinstance(fts_result, BaseException):
                raise RuntimeError("PostgreSQL FTS memory recall failed") from fts_result
            return fts_result, {}, MemoryRetrievalStrategy.POSTGRES_FTS
        if isinstance(fts_result, BaseException):
            logger.warning(
                "PostgreSQL FTS memory recall failed; using vector route: %s",
                fts_result.__class__.__name__,
            )
            fts = []
        else:
            fts = fts_result
        records, scores = _rrf_candidates(
            fts=fts,
            vector=vector_result,
            rrf_k=self.hybrid_rrf_k,
            limit=self.candidate_limit,
        )
        return records, scores, MemoryRetrievalStrategy.HYBRID

    async def _vector_candidates(
        self,
        *,
        request: MemoryRetrievalRequest,
        recall_query: str,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        now: datetime,
        provider: DenseEmbeddingProvider,
    ) -> list[tuple[EpisodicMemoryRecord, float]]:
        """Embed the query and execute the version-matched vector route."""
        query_vectors = await run_embedding(provider.embed_queries, [recall_query])
        if len(query_vectors) != 1:
            raise RuntimeError("Embedding provider returned an incomplete query batch")
        return await self.repository.search_memory_vector_candidates(
            user_id=request.user_id,
            statuses=statuses,
            memory_types=memory_types,
            query_vector=query_vectors[0],
            embedding_revision=provider.model_revision,
            minimum_similarity=self.vector_minimum_similarity,
            now=now,
            limit=self.candidate_limit,
        )

    async def _recent_candidates(
        self,
        *,
        request: MemoryRetrievalRequest,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        now: datetime,
    ) -> tuple[list[EpisodicMemoryRecord], dict[str, float], MemoryRetrievalStrategy]:
        candidates = await self.repository.search_memory_candidates(
            user_id=request.user_id,
            statuses=statuses,
            memory_types=memory_types,
            scenario_type=request.scenario_type,
            require_scenario_match=False,
            query_terms=(),
            now=now,
            limit=self.candidate_limit,
        )
        return candidates, {}, MemoryRetrievalStrategy.SQL_TEXT


def _rrf_candidates(
    *,
    fts: list[EpisodicMemoryRecord],
    vector: list[tuple[EpisodicMemoryRecord, float]],
    rrf_k: int,
    limit: int,
) -> tuple[list[EpisodicMemoryRecord], dict[str, float]]:
    """Fuse indexed route ranks without mixing incompatible raw scores."""
    records: dict[str, EpisodicMemoryRecord] = {}
    scores: dict[str, float] = {}
    active_routes = int(bool(fts)) + int(bool(vector))
    if active_routes == 0:
        return [], {}
    for rank, record in enumerate(fts, start=1):
        records[record.memory_id] = record
        scores[record.memory_id] = scores.get(record.memory_id, 0.0) + 1.0 / (
            rrf_k + rank
        )
    for rank, (record, _similarity) in enumerate(vector, start=1):
        records[record.memory_id] = record
        scores[record.memory_id] = scores.get(record.memory_id, 0.0) + 1.0 / (
            rrf_k + rank
        )
    maximum = active_routes / (rrf_k + 1)
    normalized = {
        memory_id: round(score / maximum, 6)
        for memory_id, score in scores.items()
    }
    ordered_ids = sorted(
        records,
        key=lambda memory_id: (
            normalized[memory_id],
            records[memory_id].occurred_at,
            memory_id,
        ),
        reverse=True,
    )[:limit]
    return [records[memory_id] for memory_id in ordered_ids], {
        memory_id: normalized[memory_id] for memory_id in ordered_ids
    }


def rank_memory_candidates(
    *,
    request: MemoryRetrievalRequest,
    candidates: list[EpisodicMemoryRecord],
    now: datetime,
    token_estimator: TokenEstimator,
    token_budget: int,
    fusion_scores: dict[str, float] | None = None,
) -> tuple[list[MemoryRetrievalHit], int]:
    """Pure strategy comparison used by runtime and fixed offline evaluations."""
    eligible = order_memory_candidates(
        request=request,
        candidates=candidates,
        now=now,
        fusion_scores=fusion_scores,
    )

    hits: list[MemoryRetrievalHit] = []
    used_tokens = 0
    for record, score in eligible:
        remaining = token_budget - used_tokens
        if remaining <= 0 or len(hits) >= request.limit:
            break
        summary, cost = fit_memory_summary(
            record.summary,
            memory_type=record.memory_type.value,
            remaining_tokens=remaining,
            estimator=token_estimator,
        )
        if summary is None:
            continue
        hits.append(
            MemoryRetrievalHit(
                memory_id=record.memory_id,
                memory_type=record.memory_type,
                summary=summary,
                scenario_type=record.scenario_type,
                scenario_id=record.scenario_id,
                practice_thread_id=record.practice_thread_id,
                skill_codes=record.skill_codes,
                status=record.status,
                occurred_at=record.occurred_at,
                score=score,
                estimated_tokens=cost,
            )
        )
        used_tokens += cost
    return hits, len(eligible)


def order_memory_candidates(
    *,
    request: MemoryRetrievalRequest,
    candidates: list[EpisodicMemoryRecord],
    now: datetime,
    fusion_scores: dict[str, float] | None = None,
) -> list[tuple[EpisodicMemoryRecord, MemoryRetrievalScore]]:
    """Return eligible candidates in production score order before token fitting."""
    timestamp = _as_utc(now)
    query_terms = lexical_terms(request.query)
    eligibility_query_terms = lexical_terms(request.query)
    eligible: list[tuple[EpisodicMemoryRecord, MemoryRetrievalScore]] = []
    for record in candidates:
        if not candidate_is_eligible(
            record=record,
            request=request,
            now=timestamp,
            query_terms=eligibility_query_terms,
        ):
            continue
        score = _score_record(
            record=record,
            request=request,
            query_terms=query_terms,
            now=timestamp,
            fusion_score=(fusion_scores or {}).get(record.memory_id, 0.0),
        )
        if (
            request.strategy == MemoryRetrievalStrategy.SQL_TEXT
            and score.lexical < 0.08
            and score.continuity == 0.0
        ):
            continue
        if (
            record.status == MemoryRecordStatus.ARCHIVED
            and (
                score.lexical < 0.18
                or (score.continuity == 0.0 and score.scenario < 1.0)
            )
        ):
            continue
        eligible.append((record, score))
    eligible.sort(
        key=lambda item: (
            item[1].total,
            item[0].occurred_at,
            item[0].memory_id,
        ),
        reverse=True,
    )
    return eligible


def candidate_is_eligible(
    *,
    record: EpisodicMemoryRecord,
    request: MemoryRetrievalRequest,
    now: datetime,
    query_terms: set[str],
) -> bool:
    """Defense-in-depth eligibility independent of repository correctness."""
    if (
        _HARD_FILTER.evaluate(record=record, request=request, now=now)
        != MemoryHardFilterReason.ALLOWED
    ):
        return False
    if request.strategy in {
        MemoryRetrievalStrategy.SQL_TEXT,
        MemoryRetrievalStrategy.POSTGRES_FTS,
    }:
        record_terms = lexical_terms(
            record.summary,
            record.scenario_type,
        )
        continuity = _continuity_score(record, request)
        skill_overlap = _skill_overlap_score(record, request)
        if (
            not query_terms.intersection(record_terms)
            and continuity == 0.0
            and skill_overlap == 0.0
        ):
            return False
    return True


def _score_record(
    *,
    record: EpisodicMemoryRecord,
    request: MemoryRetrievalRequest,
    query_terms: set[str],
    now: datetime,
    fusion_score: float = 0.0,
) -> MemoryRetrievalScore:
    record_terms = lexical_terms(record.summary)
    lexical = (
        len(query_terms.intersection(record_terms)) / len(query_terms)
        if query_terms
        else 0.0
    )
    scenario = (
        1.0
        if request.scenario_type is not None
        and record.scenario_type == request.scenario_type
        else 0.35 if record.scenario_type is None else 0.0
    )
    continuity = _continuity_score(record, request)
    skill_overlap = _skill_overlap_score(record, request)
    age_days = max(0.0, (now - _as_utc(record.occurred_at)).total_seconds() / 86400)
    recency = max(0.0, 1.0 - age_days / 365.0)
    if record.last_retrieved_at is None:
        novelty = 1.0
    else:
        retrieval_age_days = max(
            0.0,
            (now - _as_utc(record.last_retrieved_at)).total_seconds() / 86400,
        )
        novelty = min(1.0, retrieval_age_days / 30.0)
    weights = {
        MemoryRetrievalStrategy.RECENT: (
            0.10, 0.05, 0.25, 0.15, 0.25, 0.10, 0.10
        ),
        MemoryRetrievalStrategy.METADATA: (
            0.10, 0.05, 0.30, 0.25, 0.10, 0.10, 0.10
        ),
        MemoryRetrievalStrategy.SQL_TEXT: (
            0.30, 0.05, 0.25, 0.20, 0.05, 0.05, 0.10
        ),
        MemoryRetrievalStrategy.POSTGRES_FTS: (
            0.30, 0.05, 0.25, 0.20, 0.05, 0.05, 0.10
        ),
        MemoryRetrievalStrategy.HYBRID: (
            0.20, 0.05, 0.20, 0.15, 0.05, 0.05, 0.10, 0.20
        ),
        MemoryRetrievalStrategy.DENSE_ONLY: (
            0.20, 0.05, 0.20, 0.15, 0.05, 0.05, 0.10, 0.20
        ),
    }[request.strategy]
    total = (
        lexical * weights[0]
        + scenario * weights[1]
        + continuity * weights[2]
        + skill_overlap * weights[3]
        + recency * weights[4]
        + novelty * weights[5]
        + record.confidence * weights[6]
        + fusion_score * (weights[7] if len(weights) > 7 else 0.0)
    )
    if record.status == MemoryRecordStatus.ARCHIVED:
        total *= 0.9
    return MemoryRetrievalScore(
        lexical=round(min(1.0, lexical), 6),
        scenario=round(scenario, 6),
        recency=round(recency, 6),
        novelty=round(novelty, 6),
        confidence=round(record.confidence, 6),
        continuity=round(continuity, 6),
        skill_overlap=round(skill_overlap, 6),
        fusion=round(min(max(fusion_score, 0.0), 1.0), 6),
        total=round(min(1.0, total), 6),
    )


def _continuity_score(
    record: EpisodicMemoryRecord,
    request: MemoryRetrievalRequest,
) -> float:
    if (
        request.practice_thread_id is not None
        and record.practice_thread_id == request.practice_thread_id
    ):
        return 1.0
    if request.scenario_id is not None and record.scenario_id == request.scenario_id:
        return 0.8
    return 0.0


def _skill_overlap_score(
    record: EpisodicMemoryRecord,
    request: MemoryRetrievalRequest,
) -> float:
    requested = set(request.skill_codes)
    if not requested:
        return 0.0
    overlap = requested.intersection(record.skill_codes)
    return len(overlap) / len(requested)


def fit_memory_summary(
    summary: str,
    *,
    memory_type: str,
    remaining_tokens: int,
    estimator: TokenEstimator,
) -> tuple[str | None, int]:
    prefix = f"{memory_type}:"
    full_cost = estimator.count(prefix + summary)
    if full_cost <= remaining_tokens:
        return summary, full_cost
    if remaining_tokens <= estimator.count(prefix + "…"):
        return None, 0
    low, high = 1, len(summary)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = summary[:middle].rstrip() + "…"
        if estimator.count(prefix + candidate) <= remaining_tokens:
            low = middle
        else:
            high = middle - 1
    truncated = summary[:low].rstrip() + "…"
    cost = estimator.count(prefix + truncated)
    return (truncated, cost) if cost <= remaining_tokens else (None, 0)


def _empty_result(
    request: MemoryRetrievalRequest,
    *,
    token_budget: int,
    consent_allowed: bool,
) -> MemoryRetrievalResult:
    return MemoryRetrievalResult(
        diagnostics=MemoryRetrievalDiagnostics(
            strategy=request.strategy,
            candidate_count=0,
            eligible_count=0,
            returned_count=0,
            estimated_tokens=0,
            token_budget=token_budget,
            abstained=True,
            consent_allowed=consent_allowed,
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("memory retrieval timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
