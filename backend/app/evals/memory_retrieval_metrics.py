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
from app.models_long_term_memory import MemoryRecordStatus


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

    query_recalls: list[float] = []
    query_hits: list[bool] = []
    all_relevant_results: list[bool] = []
    judged_returned_total = 0
    forbidden_label_total = 0
    forbidden_returned_total = 0
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
        expected_ok = case.all_relevance_groups_retrieved(retrieved_ids)
        predicted_abstain = not retrieved_ids
        abstain_ok = not case.expected_abstain or predicted_abstain
        passed = expected_ok and forbidden_clear and abstain_ok
        if "passed" in outcome and bool(outcome["passed"]) != passed:
            raise ValueError(
                f"stored pass result disagrees with labels for case {case.id}"
            )

        if expected:
            relevance_recall = case.relevance_recall(retrieved_ids)
            assert relevance_recall is not None
            query_recalls.append(relevance_recall)
            query_hits.append(relevance_recall > 0)
            all_relevant_results.append(relevance_recall == 1.0)
        judged_returned = set(retrieved_ids).intersection(
            expected.union(case.forbidden_memory_ids)
        )
        judged_returned_total += len(judged_returned)
        forbidden_label_total += len(case.forbidden_memory_ids)
        forbidden_returned_total += len(
            judged_returned.intersection(case.forbidden_memory_ids)
        )
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
        fixture_by_id = {
            fixture.memory_id: fixture
            for fixture in case.memories
        }
        lifecycle_forbidden = [
            memory_id
            for memory_id in case.forbidden_memory_ids
            if (
                fixture_by_id[memory_id].status
                != MemoryRecordStatus.ACTIVE
                or (
                    fixture_by_id[memory_id].expires_days_from_now is not None
                    and fixture_by_id[memory_id].expires_days_from_now <= 0
                )
            )
        ]
        if lifecycle_forbidden:
            stale_results.append(
                not set(lifecycle_forbidden).intersection(retrieved_ids)
            )
        foreign_forbidden = [
            memory_id
            for memory_id in case.forbidden_memory_ids
            if fixture_by_id[memory_id].user_id != case.user_id
        ]
        if foreign_forbidden:
            cross_user_results.append(
                not set(foreign_forbidden).intersection(retrieved_ids)
            )
        is_conflict_case = case.category in {
            "conflict",
            "conflict_or_supersession",
        }
        if is_conflict_case:
            conflict_results.append(passed)
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
        relevant_recall_at_3=ratio(sum(query_recalls), len(query_recalls)),
        relevant_mrr=ratio(reciprocal_rank_sum, reciprocal_rank_total),
        relevant_hit_at_3=_bool_metric(query_hits),
        all_relevant_recall_at_3=_bool_metric(all_relevant_results),
        forbidden_item_avoidance=ratio(
            forbidden_label_total - forbidden_returned_total,
            forbidden_label_total,
        ),
        judged_item_precision_at_3=ratio(
            judged_returned_total - forbidden_returned_total,
            judged_returned_total,
        ),
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
        latency_sample_count=len(latencies),
        mean_query_latency_ms=_mean(latencies),
        p50_query_latency_ms=_percentile(latencies, 0.50),
        p95_query_latency_ms=_percentile(latencies, 0.95),
        p99_query_latency_ms=_percentile(latencies, 0.99),
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


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        math.ceil(len(ordered) * quantile) - 1,
    )
    return round(ordered[index], 6)
