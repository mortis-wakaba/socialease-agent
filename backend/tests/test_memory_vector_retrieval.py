"""Optional real-model checks for the dense memory retrieval benchmark."""

from collections.abc import Sequence
import hashlib
import math
import os
from pathlib import Path

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
from app.evals.dense_embedding import FastEmbedBgeSmallZh


class _DeterministicEmbedding:
    """Small dependency-free embedder for scale benchmark contract tests."""

    provider_name = "deterministic-test"
    model_name = "sha256-projection"
    model_revision = "v1"
    dimensions = 8
    model_size_mb = 0.0

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    @staticmethod
    def _embed(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = [float(digest[index]) - 127.5 for index in range(8)]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values]


def test_vector_benchmark_exercises_large_candidate_corpus() -> None:
    """The always-on contract must not silently shrink back to toy scale."""
    report, outcomes = run_vector_memory_retrieval_benchmark(
        embedder=_DeterministicEmbedding(),
    )

    assert report.dataset_case_count == 59
    assert report.indexed_memory_count is not None
    assert report.indexed_memory_count >= 2000
    assert report.max_candidates_per_query is not None
    assert report.max_candidates_per_query > report.classical_candidate_window
    assert report.classical_candidate_window == 100
    assert report.scale_gate_met is True
    assert all(
        len(strategy_outcomes) == report.dataset_case_count
        for strategy_outcomes in outcomes.values()
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
    assert report.dataset_case_count >= 50
    assert report.indexed_memory_count is not None
    assert report.indexed_memory_count >= 2000
    assert report.max_candidates_per_query is not None
    assert report.max_candidates_per_query >= 2000
    assert report.classical_candidate_window == 100
    assert report.scale_gate_met is True
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
    assert vector.no_memory_abstention.score < 1.0
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


def test_local_embedding_path_fails_before_model_load_when_incomplete(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.json").touch()

    with pytest.raises(RuntimeError, match="model_optimized.onnx"):
        FastEmbedBgeSmallZh(specific_model_path=str(tmp_path))


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
