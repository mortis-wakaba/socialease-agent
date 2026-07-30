"""Shared, strategy-independent metrics for memory retrieval evaluations."""

from __future__ import annotations

import math
from typing import Any

from app.evals.metrics import ratio
from app.evals.models import (
    EvalMetric,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalStrategyReport,
)


def build_memory_retrieval_strategy_report(
    *,
    strategy: MemoryRetrievalBenchmarkStrategy,
    cases: list[MemoryRetrievalEvalCase],
    outcomes: list[dict[str, Any]],
) -> MemoryRetrievalStrategyReport:
    """Aggregate identical labels and denominators for every strategy."""
    case_by_id = {case.id: case for case in cases}
    outcome_by_id = {str(item["case_id"]): item for item in outcomes}
    if len(case_by_id) != len(cases):
        raise ValueError("memory retrieval case ids must be unique")
    if len(outcome_by_id) != len(outcomes):
        raise ValueError("memory retrieval outcome case ids must be unique")
    if set(case_by_id) != set(outcome_by_id):
        raise ValueError("memory retrieval outcomes do not match cases")

    expected_total = expected_found = 0
    reciprocal_rank_sum = 0.0
    reciprocal_rank_total = 0
    false_results: list[bool] = []
    stale_results: list[bool] = []
    conflict_results: list[bool] = []
    cross_user_results: list[bool] = []
    abstention_labels: list[bool] = []
    abstention_predictions: list[bool] = []
    budget_results: list[bool] = []
    case_results: list[bool] = []
    latencies: list[float] = []

    for case in cases:
        outcome = outcome_by_id[case.id]
        retrieved_ids = [str(value) for value in outcome["retrieved_ids"]]
        if len(retrieved_ids) != len(set(retrieved_ids)):
            raise ValueError(
                f"retrieved memory ids must be unique for case {case.id}"
            )
        expected = set(case.expected_memory_ids)
        forbidden_clear = not set(retrieved_ids).intersection(
            case.forbidden_memory_ids
        )
        expected_ok = expected.issubset(retrieved_ids)
        predicted_abstain = not retrieved_ids
        abstain_ok = not case.expected_abstain or predicted_abstain
        passed = expected_ok and forbidden_clear and abstain_ok
        if "passed" in outcome and bool(outcome["passed"]) != passed:
            raise ValueError(
                f"stored pass result disagrees with labels for case {case.id}"
            )

        expected_total += len(expected)
        expected_found += sum(memory_id in retrieved_ids for memory_id in expected)
        if expected:
            reciprocal_rank_total += 1
            rank = next(
                (
                    index
                    for index, memory_id in enumerate(retrieved_ids, start=1)
                    if memory_id in expected
                ),
                None,
            )
            if rank is not None:
                reciprocal_rank_sum += 1.0 / rank
        if case.forbidden_memory_ids:
            false_results.append(forbidden_clear)
        if case.category == "stale":
            stale_results.append(forbidden_clear)
        if case.category == "conflict":
            conflict_results.append(passed)
        if case.category == "cross_user":
            cross_user_results.append(forbidden_clear)
        abstention_labels.append(case.expected_abstain)
        abstention_predictions.append(predicted_abstain)
        budget_results.append(
            int(outcome["estimated_tokens"]) <= 256
            and len(retrieved_ids) <= 3
        )
        case_results.append(passed)
        latencies.append(float(outcome.get("query_latency_ms", 0.0)))

    true_positive_abstentions = sum(
        expected and predicted
        for expected, predicted in zip(
            abstention_labels,
            abstention_predictions,
            strict=True,
        )
    )
    expected_abstentions = sum(abstention_labels)
    predicted_abstentions = sum(abstention_predictions)
    return MemoryRetrievalStrategyReport(
        strategy=strategy,
        relevant_recall_at_3=ratio(expected_found, expected_total),
        relevant_mrr=ratio(reciprocal_rank_sum, reciprocal_rank_total),
        false_recall_avoidance=_bool_metric(false_results),
        stale_recall_avoidance=_bool_metric(stale_results),
        conflict_resolution=_bool_metric(conflict_results),
        cross_user_leakage_avoidance=_bool_metric(cross_user_results),
        no_memory_abstention=ratio(
            true_positive_abstentions,
            expected_abstentions,
        ),
        abstention_precision=ratio(
            true_positive_abstentions,
            predicted_abstentions,
        ),
        abstention_recall=ratio(
            true_positive_abstentions,
            expected_abstentions,
        ),
        context_token_budget=_bool_metric(budget_results),
        case_pass_rate=_bool_metric(case_results),
        mean_query_latency_ms=_mean(latencies),
        p95_query_latency_ms=_percentile_95(latencies),
    )


def filter_memory_retrieval_outcomes(
    *,
    cases: list[MemoryRetrievalEvalCase],
    outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select outcomes for one split while preserving its case order."""
    outcome_by_id = {str(item["case_id"]): item for item in outcomes}
    return [outcome_by_id[case.id] for case in cases]


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
