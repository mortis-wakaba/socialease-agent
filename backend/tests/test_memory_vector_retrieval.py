"""Optional real-model checks for the dense memory retrieval benchmark."""

import os

import pytest

from app.evals.metrics import ratio
from app.evals.models import (
    EvalMetric,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalStrategyReport,
)
from app.evals.vector_memory_retrieval import (
    SEMANTIC_THRESHOLD,
    passes_vector_gate,
    run_vector_memory_retrieval_benchmark,
)


@pytest.mark.vector_eval
@pytest.mark.skipif(
    os.getenv("RUN_VECTOR_EVALS", "").casefold() != "true",
    reason=(
        "Set RUN_VECTOR_EVALS=true after installing "
        "requirements-vector-eval.txt."
    ),
)
def test_real_bge_vector_and_hybrid_memory_benchmark() -> None:
    """Record honest model-backed gains and safety failures on demo cases."""
    report, outcomes = run_vector_memory_retrieval_benchmark()

    assert report.vector_evaluated is True
    assert report.hybrid_evaluated is True
    assert report.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert (
        report.embedding_model_revision
        == "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
    )
    assert report.embedding_dimensions == 512
    assert report.dataset_case_count == 15
    assert set(report.strategies) == {
        "recent",
        "metadata",
        "sql_text",
        "vector",
        "hybrid",
    }
    sql_text = report.strategies["sql_text"]
    vector = report.strategies["vector"]
    assert (
        vector.relevant_recall_at_3.score
        > sql_text.relevant_recall_at_3.score
    )
    assert vector.no_memory_abstention.score == 1.0
    assert vector.false_recall_avoidance.score < 1.0
    assert report.vector_gate_met is False
    assert (
        report.selected_strategy
        == MemoryRetrievalBenchmarkStrategy.SQL_TEXT
    )
    assert SEMANTIC_THRESHOLD == 0.50
    assert any(
        not outcome["passed"] for outcome in outcomes["vector"]
    )


def test_vector_gate_requires_recall_gain_and_perfect_safety_metrics() -> None:
    """Recall improvement must not hide false-memory safety regressions."""
    baseline = _strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.SQL_TEXT,
        recall=0.4,
        false_recall=0.9,
    )
    unsafe_vector = _strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.VECTOR,
        recall=0.6,
        false_recall=0.8,
    )
    safe_vector = _strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.VECTOR,
        recall=0.6,
        false_recall=1.0,
    )

    assert not passes_vector_gate(
        candidate=unsafe_vector,
        baseline=baseline,
    )
    assert passes_vector_gate(
        candidate=safe_vector,
        baseline=baseline,
    )


def _strategy_report(
    *,
    strategy: MemoryRetrievalBenchmarkStrategy,
    recall: float,
    false_recall: float,
) -> MemoryRetrievalStrategyReport:
    perfect = ratio(1, 1)
    return MemoryRetrievalStrategyReport(
        strategy=strategy,
        relevant_recall_at_3=EvalMetric(
            total=1,
            passed=recall,
            score=recall,
        ),
        false_recall_avoidance=EvalMetric(
            total=1,
            passed=false_recall,
            score=false_recall,
        ),
        stale_recall_avoidance=perfect,
        conflict_resolution=perfect,
        cross_user_leakage_avoidance=perfect,
        no_memory_abstention=perfect,
        context_token_budget=perfect,
        case_pass_rate=perfect,
        p95_query_latency_ms=10.0,
    )
