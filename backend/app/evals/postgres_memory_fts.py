"""Real PostgreSQL FTS baseline for episodic-memory retrieval evaluations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.db.postgres.long_term_memory_repository import (
    PostgresLongTermMemoryRepository,
)
from app.db.postgres.memory_retrieval_eval import (
    PostgresMemoryRetrievalEvalAdapter,
)
from app.evals.memory_retrieval import EVAL_NOW, EVAL_TOKEN_BUDGET
from app.evals.memory_retrieval_dataset import (
    MemoryRetrievalDataset,
    MemoryRetrievalEvalSplit,
)
from app.evals.memory_retrieval_metrics import (
    build_memory_retrieval_strategy_report,
)
from app.evals.models import (
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalStrategyReport,
)
from app.memory.hard_filter import MemoryHardFilter
from app.memory.retriever import fit_memory_summary
from app.memory.text_semantics import lexical_terms
from app.memory.token_estimator import ConservativeTokenEstimator
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryRecordStatus,
    MemoryRetrievalRequest,
)


async def evaluate_postgres_fts(
    *,
    dataset: MemoryRetrievalDataset,
    repository: PostgresLongTermMemoryRepository,
    fixture_adapter: PostgresMemoryRetrievalEvalAdapter,
) -> tuple[MemoryRetrievalStrategyReport, list[dict[str, Any]], float, float]:
    """Load isolated corpora and measure real FTS query I/O and ranking."""
    records, mappings = _namespace_dataset(dataset)
    load_started = perf_counter()
    await fixture_adapter.replace_records(records)
    load_latency_ms = _elapsed_ms(load_started)
    warmup_latency_ms = await _warm_postgres_fts(
        dataset=dataset,
        repository=repository,
        mappings=mappings,
    )
    hard_filter = MemoryHardFilter()
    estimator = ConservativeTokenEstimator()
    outcomes: list[dict[str, Any]] = []
    try:
        for case in dataset.cases:
            mapping = mappings[case.id]
            request = MemoryRetrievalRequest(
                user_id=mapping.query_user_id,
                query=case.query,
                allowed_memory_types=case.allowed_memory_types,
                scenario_type=case.scenario_type,
                scenario_id=case.scenario_id,
                practice_thread_id=case.practice_thread_id,
                skill_codes=case.skill_codes,
                include_archived=case.include_archived,
            )
            query_terms = tuple(
                sorted(lexical_terms(case.query), key=lambda item: (len(item), item))
            )
            started = perf_counter()
            fetch_started = perf_counter()
            candidates = await repository.search_memory_fts_candidates(
                user_id=request.user_id,
                statuses=mapping.allowed_statuses,
                memory_types=tuple(request.allowed_memory_types),
                query_terms=query_terms,
                now=EVAL_NOW,
                limit=100,
            )
            fetch_latency_ms = _elapsed_ms(fetch_started)
            filter_started = perf_counter()
            eligible, _ = hard_filter.filter(
                records=candidates,
                request=request,
                now=EVAL_NOW,
            )
            filter_latency_ms = _elapsed_ms(filter_started)
            fit_started = perf_counter()
            retrieved_ids: list[str] = []
            used_tokens = 0
            for record in eligible[:3]:
                summary, cost = fit_memory_summary(
                    record.summary,
                    memory_type=record.memory_type.value,
                    remaining_tokens=EVAL_TOKEN_BUDGET - used_tokens,
                    estimator=estimator,
                )
                if summary is None:
                    continue
                retrieved_ids.append(mapping.original_id_by_namespaced[record.memory_id])
                used_tokens += cost
            fit_latency_ms = _elapsed_ms(fit_started)
            query_latency_ms = _elapsed_ms(started)
            forbidden_clear = not set(retrieved_ids).intersection(
                case.forbidden_memory_ids
            )
            passed = (
                all(item in retrieved_ids for item in case.expected_memory_ids)
                and forbidden_clear
                and (not case.expected_abstain or not retrieved_ids)
            )
            outcomes.append(
                {
                    "case_id": case.id,
                    "category": case.category,
                    "retrieved_ids": retrieved_ids,
                    "expected_ids": case.expected_memory_ids,
                    "forbidden_ids": case.forbidden_memory_ids,
                    "expected_abstain": case.expected_abstain,
                    "candidate_count": len(dataset.records_by_case[case.id]),
                    "eligible_count": len(eligible),
                    "estimated_tokens": used_tokens,
                    "query_latency_ms": round(query_latency_ms, 6),
                    "stage_latency_ms": {
                        "postgres_fts_fetch": round(fetch_latency_ms, 6),
                        "hard_filter": round(filter_latency_ms, 6),
                        "token_fit": round(fit_latency_ms, 6),
                    },
                    "passed": passed,
                }
            )
    finally:
        await fixture_adapter.clear()
    return (
        build_memory_retrieval_strategy_report(
            strategy=MemoryRetrievalBenchmarkStrategy.POSTGRES_FTS,
            cases=dataset.cases,
            outcomes=outcomes,
        ),
        outcomes,
        load_latency_ms,
        warmup_latency_ms,
    )


@dataclass(frozen=True)
class _CaseMapping:
    """Content-free namespace mapping for one case corpus."""

    query_user_id: str
    original_id_by_namespaced: dict[str, str]
    allowed_statuses: tuple[MemoryRecordStatus, ...]


def _namespace_dataset(
    dataset: MemoryRetrievalDataset,
) -> tuple[list[EpisodicMemoryRecord], dict[str, _CaseMapping]]:
    """Isolate functional cases while preserving one shared scale corpus."""
    records_by_key: dict[tuple[str, str], EpisodicMemoryRecord] = {}
    mappings: dict[str, _CaseMapping] = {}
    split_by_case = {
        case.id: split
        for split, cases in dataset.splits.items()
        for case in cases
    }
    for case in dataset.cases:
        scope = (
            "scale"
            if split_by_case[case.id] == MemoryRetrievalEvalSplit.SCALE
            else case.id
        )
        user_map: dict[str, str] = {}
        id_map: dict[str, str] = {}
        for record in dataset.records_by_case[case.id]:
            namespaced_user = user_map.setdefault(
                record.user_id,
                _namespaced("memory_eval_", scope, record.user_id),
            )
            namespaced_id = _namespaced(
                "memory_eval_record_",
                scope,
                record.user_id,
                record.memory_id,
            )
            id_map[namespaced_id] = record.memory_id
            namespaced = record.model_copy(
                update={
                    "memory_id": namespaced_id,
                    "user_id": namespaced_user,
                    "idempotency_key": hashlib.sha256(
                        f"{namespaced_user}:{namespaced_id}".encode()
                    ).hexdigest(),
                }
            )
            records_by_key[(namespaced_user, namespaced_id)] = namespaced
        allowed_statuses = [MemoryRecordStatus.ACTIVE]
        if case.include_archived:
            allowed_statuses.append(MemoryRecordStatus.ARCHIVED)
        mappings[case.id] = _CaseMapping(
            query_user_id=user_map.get(
                case.user_id,
                _namespaced("memory_eval_", scope, case.user_id),
            ),
            original_id_by_namespaced=id_map,
            allowed_statuses=tuple(allowed_statuses),
        )
    return list(records_by_key.values()), mappings


def _namespaced(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\\x1f".join(parts).encode()).hexdigest()
    return prefix + digest


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000


async def _warm_postgres_fts(
    *,
    dataset: MemoryRetrievalDataset,
    repository: PostgresLongTermMemoryRepository,
    mappings: dict[str, _CaseMapping],
) -> float:
    """Warm the connection, analyzer and GIN path outside query latency."""
    if not dataset.cases:
        return 0.0
    case = dataset.cases[0]
    mapping = mappings[case.id]
    query_terms = tuple(
        sorted(lexical_terms(case.query), key=lambda item: (len(item), item))
    )
    started = perf_counter()
    await repository.search_memory_fts_candidates(
        user_id=mapping.query_user_id,
        statuses=mapping.allowed_statuses,
        memory_types=tuple(case.allowed_memory_types),
        query_terms=query_terms,
        now=EVAL_NOW,
        limit=100,
    )
    return _elapsed_ms(started)
