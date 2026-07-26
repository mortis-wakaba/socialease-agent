"""Measured dense and hybrid episodic-memory retrieval benchmark."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from time import perf_counter
from typing import Any

from app.evals.dense_embedding import (
    DenseEmbeddingProvider,
    FastEmbedBgeSmallZh,
)
from app.evals.loader import (
    load_memory_retrieval_cases,
    load_memory_vector_challenge_cases,
)
from app.evals.memory_retrieval import (
    EVAL_NOW,
    EVAL_TOKEN_BUDGET,
    evaluate_classical_strategy,
    record_from_fixture,
)
from app.evals.metrics import ratio
from app.evals.models import (
    EvalMetric,
    MemoryRetrievalBenchmarkReport,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalStrategyReport,
)
from app.memory.retriever import (
    candidate_is_eligible,
    fit_memory_summary,
    lexical_terms,
)
from app.memory.token_estimator import ConservativeTokenEstimator
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryRecordStatus,
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
)


# Calibrated above the strongest fixed no-memory hard negative (0.4791).
# BGE v1.5 absolute cosine scores are task-dependent; ranking remains primary.
SEMANTIC_THRESHOLD = 0.50
HYBRID_SEMANTIC_WEIGHT = 0.75
VECTOR_RECALL_GAIN_GATE = 0.10
WARM_P95_LATENCY_GATE_MS = 250.0


@dataclass(frozen=True)
class _SemanticCandidate:
    record: EpisodicMemoryRecord
    semantic_score: float
    lexical_score: float
    final_score: float


def run_vector_memory_retrieval_benchmark(
    *,
    embedder: DenseEmbeddingProvider | None = None,
    cold_start_latency_ms: float | None = None,
) -> tuple[MemoryRetrievalBenchmarkReport, dict[str, list[dict[str, Any]]]]:
    """Run A/B/C/D/E on fixed safety cases plus semantic hard negatives."""
    cases = [
        *load_memory_retrieval_cases(),
        *load_memory_vector_challenge_cases(),
    ]
    provider = embedder or FastEmbedBgeSmallZh()
    strategies: dict[str, MemoryRetrievalStrategyReport] = {}
    outcomes_by_strategy: dict[str, list[dict[str, Any]]] = {}

    for strategy in (
        MemoryRetrievalStrategy.RECENT,
        MemoryRetrievalStrategy.METADATA,
        MemoryRetrievalStrategy.SQL_TEXT,
    ):
        report, outcomes = evaluate_classical_strategy(
            cases,
            strategy=strategy,
        )
        strategies[strategy.value] = report
        outcomes_by_strategy[strategy.value] = outcomes

    records_by_case = {
        case.id: [record_from_fixture(item) for item in case.memories]
        for case in cases
    }
    unique_summaries = list(
        dict.fromkeys(
            record.summary
            for records in records_by_case.values()
            for record in records
        )
    )
    document_started = perf_counter()
    document_vectors = provider.embed_documents(unique_summaries)
    document_embedding_latency_ms = (
        perf_counter() - document_started
    ) * 1000
    document_vector_by_summary = dict(
        zip(unique_summaries, document_vectors, strict=True)
    )

    for strategy in (
        MemoryRetrievalBenchmarkStrategy.VECTOR,
        MemoryRetrievalBenchmarkStrategy.HYBRID,
    ):
        report, outcomes = _evaluate_semantic_strategy(
            cases,
            records_by_case=records_by_case,
            document_vector_by_summary=document_vector_by_summary,
            embedder=provider,
            strategy=strategy,
        )
        strategies[strategy.value] = report
        outcomes_by_strategy[strategy.value] = outcomes

    sql_report = strategies[MemoryRetrievalBenchmarkStrategy.SQL_TEXT.value]
    semantic_reports = [
        strategies[MemoryRetrievalBenchmarkStrategy.VECTOR.value],
        strategies[MemoryRetrievalBenchmarkStrategy.HYBRID.value],
    ]
    best_semantic = max(
        semantic_reports,
        key=lambda item: (
            item.relevant_recall_at_3.score,
            item.false_recall_avoidance.score,
            item.case_pass_rate.score,
            -item.p95_query_latency_ms,
        ),
    )
    vector_gate_met = passes_vector_gate(
        candidate=best_semantic,
        baseline=sql_report,
    )
    selected_strategy = (
        best_semantic.strategy
        if vector_gate_met
        else MemoryRetrievalBenchmarkStrategy.SQL_TEXT
    )
    indexed_memory_count = len(unique_summaries)
    report = MemoryRetrievalBenchmarkReport(
        selected_strategy=selected_strategy,
        strategies=strategies,
        dataset_case_count=len(cases),
        embedder_provider=provider.provider_name,
        embedding_model=provider.model_name,
        embedding_model_revision=provider.model_revision,
        embedding_dimensions=provider.dimensions,
        model_size_mb=provider.model_size_mb,
        cold_start_latency_ms=cold_start_latency_ms,
        indexed_memory_count=indexed_memory_count,
        estimated_index_bytes=indexed_memory_count * provider.dimensions * 4,
        document_embedding_latency_ms=round(
            document_embedding_latency_ms,
            6,
        ),
        vector_evaluated=True,
        hybrid_evaluated=True,
        vector_gate_met=vector_gate_met,
    )
    return report, outcomes_by_strategy


def _evaluate_semantic_strategy(
    cases: list[MemoryRetrievalEvalCase],
    *,
    records_by_case: dict[str, list[EpisodicMemoryRecord]],
    document_vector_by_summary: dict[str, list[float]],
    embedder: DenseEmbeddingProvider,
    strategy: MemoryRetrievalBenchmarkStrategy,
) -> tuple[MemoryRetrievalStrategyReport, list[dict[str, Any]]]:
    estimator = ConservativeTokenEstimator()
    expected_total = 0
    expected_found = 0
    false_results: list[bool] = []
    stale_results: list[bool] = []
    conflict_results: list[bool] = []
    cross_user_results: list[bool] = []
    abstention_results: list[bool] = []
    budget_results: list[bool] = []
    latencies_ms: list[float] = []
    outcomes: list[dict[str, Any]] = []

    for case in cases:
        started = perf_counter()
        query_vector = embedder.embed_queries([case.query])[0]
        request = MemoryRetrievalRequest(
            user_id=case.user_id,
            query=case.query,
            allowed_memory_types=case.allowed_memory_types,
            scenario_type=case.scenario_type,
            include_archived=case.include_archived,
            strategy=MemoryRetrievalStrategy.METADATA,
        )
        query_terms = lexical_terms(case.query)
        ranked: list[_SemanticCandidate] = []
        for record in records_by_case[case.id]:
            if not candidate_is_eligible(
                record=record,
                request=request,
                now=EVAL_NOW,
                query_terms=query_terms,
            ):
                continue
            semantic = _cosine(
                query_vector,
                document_vector_by_summary[record.summary],
            )
            if semantic < SEMANTIC_THRESHOLD:
                continue
            if (
                record.status == MemoryRecordStatus.ARCHIVED
                and (
                    record.scenario_type != request.scenario_type
                    or semantic < SEMANTIC_THRESHOLD + 0.03
                )
            ):
                continue
            record_terms = lexical_terms(record.summary)
            lexical = (
                len(query_terms.intersection(record_terms)) / len(query_terms)
                if query_terms
                else 0.0
            )
            final = (
                semantic
                if strategy == MemoryRetrievalBenchmarkStrategy.VECTOR
                else (
                    semantic * HYBRID_SEMANTIC_WEIGHT
                    + lexical * (1.0 - HYBRID_SEMANTIC_WEIGHT)
                )
            )
            ranked.append(
                _SemanticCandidate(
                    record=record,
                    semantic_score=semantic,
                    lexical_score=lexical,
                    final_score=final,
                )
            )
        ranked.sort(
            key=lambda item: (
                item.final_score,
                item.record.occurred_at,
                item.record.memory_id,
            ),
            reverse=True,
        )
        retrieved_ids: list[str] = []
        estimated_tokens = 0
        score_details: list[dict[str, object]] = []
        for item in ranked:
            if len(retrieved_ids) >= request.limit:
                break
            summary, cost = fit_memory_summary(
                item.record.summary,
                memory_type=item.record.memory_type.value,
                remaining_tokens=EVAL_TOKEN_BUDGET - estimated_tokens,
                estimator=estimator,
            )
            if summary is None:
                continue
            retrieved_ids.append(item.record.memory_id)
            estimated_tokens += cost
            score_details.append(
                {
                    "memory_id": item.record.memory_id,
                    "semantic": round(item.semantic_score, 6),
                    "lexical": round(item.lexical_score, 6),
                    "final": round(item.final_score, 6),
                }
            )
        latencies_ms.append((perf_counter() - started) * 1000)

        expected_total += len(case.expected_memory_ids)
        expected_found += sum(
            memory_id in retrieved_ids for memory_id in case.expected_memory_ids
        )
        forbidden_clear = not set(retrieved_ids).intersection(
            case.forbidden_memory_ids
        )
        if case.forbidden_memory_ids:
            false_results.append(forbidden_clear)
        if case.category == "stale":
            stale_results.append(forbidden_clear)
        if case.category == "conflict":
            conflict_results.append(forbidden_clear and not retrieved_ids)
        if case.category == "cross_user":
            cross_user_results.append(forbidden_clear)
        if case.category == "abstention":
            abstention_results.append(not retrieved_ids)
        budget_results.append(
            estimated_tokens <= EVAL_TOKEN_BUDGET
            and len(retrieved_ids) <= request.limit
        )
        expected_ok = all(
            memory_id in retrieved_ids for memory_id in case.expected_memory_ids
        )
        abstain_ok = not case.expected_abstain or not retrieved_ids
        outcomes.append(
            {
                "case_id": case.id,
                "category": case.category,
                "retrieved_ids": retrieved_ids,
                "expected_ids": case.expected_memory_ids,
                "forbidden_ids": case.forbidden_memory_ids,
                "expected_abstain": case.expected_abstain,
                "eligible_count": len(ranked),
                "estimated_tokens": estimated_tokens,
                "score_details": score_details,
                "passed": expected_ok and forbidden_clear and abstain_ok,
            }
        )

    return (
        MemoryRetrievalStrategyReport(
            strategy=strategy,
            relevant_recall_at_3=ratio(expected_found, expected_total),
            false_recall_avoidance=_bool_metric(false_results),
            stale_recall_avoidance=_bool_metric(stale_results),
            conflict_resolution=_bool_metric(conflict_results),
            cross_user_leakage_avoidance=_bool_metric(cross_user_results),
            no_memory_abstention=_bool_metric(abstention_results),
            context_token_budget=_bool_metric(budget_results),
            case_pass_rate=_bool_metric(
                [bool(outcome["passed"]) for outcome in outcomes]
            ),
            mean_query_latency_ms=_mean(latencies_ms),
            p95_query_latency_ms=_percentile_95(latencies_ms),
        ),
        outcomes,
    )


def passes_vector_gate(
    *,
    candidate: MemoryRetrievalStrategyReport,
    baseline: MemoryRetrievalStrategyReport,
) -> bool:
    safety_metrics = (
        "false_recall_avoidance",
        "stale_recall_avoidance",
        "conflict_resolution",
        "cross_user_leakage_avoidance",
        "no_memory_abstention",
        "context_token_budget",
    )
    return (
        candidate.relevant_recall_at_3.score
        >= baseline.relevant_recall_at_3.score + VECTOR_RECALL_GAIN_GATE
        and all(
            getattr(candidate, name).score >= 1.0
            for name in safety_metrics
        )
        and candidate.case_pass_rate.score >= baseline.case_pass_rate.score
        and candidate.p95_query_latency_ms <= WARM_P95_LATENCY_GATE_MS
    )


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Embedding dimensions do not match")
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _bool_metric(values: list[bool]) -> EvalMetric:
    return ratio(sum(values), len(values))


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 6)


def main() -> None:
    """Run the optional model-backed benchmark and print full evidence."""
    started = perf_counter()
    embedder = FastEmbedBgeSmallZh()
    cold_start_latency_ms = (perf_counter() - started) * 1000
    report, outcomes = run_vector_memory_retrieval_benchmark(
        embedder=embedder,
        cold_start_latency_ms=cold_start_latency_ms,
    )
    print(
        json.dumps(
            {
                "report": report.model_dump(mode="json"),
                "outcomes": outcomes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
