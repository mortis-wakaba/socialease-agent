"""Deterministic A/B/C benchmark for episodic-memory retrieval."""

from datetime import datetime, timedelta, timezone
import hashlib
from time import perf_counter
from typing import Any

from app.evals.loader import load_memory_retrieval_cases
from app.evals.metrics import ratio
from app.evals.models import (
    EvalMetric,
    MemoryRetrievalBenchmarkReport,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalFixture,
    MemoryRetrievalStrategyReport,
)
from app.memory.retriever import rank_memory_candidates
from app.memory.token_estimator import ConservativeTokenEstimator
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
    MemorySourceType,
)


EVAL_NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
EVAL_TOKEN_BUDGET = 256


def run_memory_retrieval_benchmark(
) -> tuple[MemoryRetrievalBenchmarkReport, list[dict[str, Any]]]:
    """Compare recent, metadata and SQL-text strategies on fixed Chinese cases."""
    cases = load_memory_retrieval_cases()
    strategy_reports: dict[str, MemoryRetrievalStrategyReport] = {}
    selected_outcomes: list[dict[str, Any]] = []
    for strategy in MemoryRetrievalStrategy:
        report, outcomes = evaluate_classical_strategy(
            cases,
            strategy=strategy,
        )
        strategy_reports[strategy.value] = report
        if strategy == MemoryRetrievalStrategy.SQL_TEXT:
            selected_outcomes = outcomes
    benchmark = MemoryRetrievalBenchmarkReport(
        selected_strategy=MemoryRetrievalBenchmarkStrategy.SQL_TEXT,
        strategies=strategy_reports,
        dataset_case_count=len(cases),
        vector_evaluated=False,
        hybrid_evaluated=False,
        vector_gate_met=False,
    )
    return benchmark, selected_outcomes


def evaluate_classical_strategy(
    cases: list[MemoryRetrievalEvalCase],
    *,
    strategy: MemoryRetrievalStrategy,
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
        request = MemoryRetrievalRequest(
            user_id=case.user_id,
            query=case.query,
            allowed_memory_types=case.allowed_memory_types,
            scenario_type=case.scenario_type,
            scenario_id=case.scenario_id,
            practice_thread_id=case.practice_thread_id,
            skill_codes=case.skill_codes,
            include_archived=case.include_archived,
            strategy=strategy,
        )
        candidates = [record_from_fixture(item) for item in case.memories]
        started = perf_counter()
        hits, eligible_count = rank_memory_candidates(
            request=request,
            candidates=candidates,
            now=EVAL_NOW,
            token_estimator=estimator,
            token_budget=EVAL_TOKEN_BUDGET,
        )
        latencies_ms.append((perf_counter() - started) * 1000)
        retrieved_ids = [hit.memory_id for hit in hits]
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
        estimated_tokens = sum(hit.estimated_tokens for hit in hits)
        budget_results.append(
            estimated_tokens <= EVAL_TOKEN_BUDGET and len(hits) <= 3
        )
        expected_ok = all(
            memory_id in retrieved_ids for memory_id in case.expected_memory_ids
        )
        abstain_ok = not case.expected_abstain or not retrieved_ids
        passed = expected_ok and forbidden_clear and abstain_ok
        outcomes.append(
            {
                "case_id": case.id,
                "category": case.category,
                "retrieved_ids": retrieved_ids,
                "expected_ids": case.expected_memory_ids,
                "forbidden_ids": case.forbidden_memory_ids,
                "expected_abstain": case.expected_abstain,
                "eligible_count": eligible_count,
                "estimated_tokens": estimated_tokens,
                "passed": passed,
            }
        )

    return (
        MemoryRetrievalStrategyReport(
            strategy=MemoryRetrievalBenchmarkStrategy(strategy.value),
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


def record_from_fixture(
    fixture: MemoryRetrievalFixture,
) -> EpisodicMemoryRecord:
    occurred_at = EVAL_NOW - timedelta(days=fixture.occurred_days_ago)
    expires_at = (
        EVAL_NOW + timedelta(days=fixture.expires_days_from_now)
        if fixture.expires_days_from_now is not None
        else None
    )
    content_hash = hashlib.sha256(
        fixture.summary.encode("utf-8")
    ).hexdigest()
    idempotency_key = hashlib.sha256(
        f"{fixture.user_id}:{fixture.memory_id}".encode("utf-8")
    ).hexdigest()
    return EpisodicMemoryRecord(
        memory_id=fixture.memory_id,
        user_id=fixture.user_id,
        memory_type=fixture.memory_type,
        summary=fixture.summary,
        scenario_type=fixture.scenario_type,
        scenario_id=fixture.scenario_id,
        practice_thread_id=fixture.practice_thread_id,
        skill_codes=fixture.skill_codes,
        context_tags=fixture.context_tags,
        source_type=MemorySourceType.USER_CONFIRMED,
        source_id=fixture.source_id,
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=fixture.confidence,
        status=fixture.status,
        occurred_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
        expires_at=expires_at,
        consent_version="practice-summary-v1",
        content_hash=content_hash,
        idempotency_key=idempotency_key,
    )


def _bool_metric(values: list[bool]) -> EvalMetric:
    return ratio(sum(values), len(values))


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95)))
    return round(ordered[index], 6)
