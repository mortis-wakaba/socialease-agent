"""Always-on contracts for the memory retrieval v2 ablation runner."""

from collections.abc import Sequence
import hashlib
import math

from app.evals.memory_retrieval_ablation import (
    passes_ablation_gate,
    run_memory_retrieval_ablation,
)
from app.evals.models import (
    EvalMetric,
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalFixture,
    MemoryRetrievalStrategyReport,
)
from app.models_long_term_memory import MemoryType


class _Embedding:
    provider_name = "deterministic_test"
    model_name = "hash"
    model_revision = "1"
    dimensions = 8
    model_size_mb = 0.0

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    @staticmethod
    def _embed(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        values = [float(value) - 127.5 for value in digest[:8]]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values]


class _Reranker:
    provider_name = "deterministic_test"
    model_name = "lexical_pair"
    model_revision = "1"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [
            4.0 if any(term in document for term in ("观点", "拒绝")) else -2.0
            for document in documents
        ]


def _cases() -> list[MemoryRetrievalEvalCase]:
    return [
        MemoryRetrievalEvalCase(
            id="relevant",
            category="semantic_relevance",
            user_id="eval_user",
            query="发言时怎样表达观点？",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            memories=[
                MemoryRetrievalFixture(
                    memory_id="target",
                    user_id="eval_user",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary="先说一句核心观点，再补充理由。",
                    occurred_days_ago=2,
                ),
                MemoryRetrievalFixture(
                    memory_id="negative",
                    user_id="eval_user",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary="先确认活动开始时间。",
                    occurred_days_ago=1,
                ),
            ],
            expected_memory_ids=["target"],
            demo=True,
        ),
        MemoryRetrievalEvalCase(
            id="abstain",
            category="abstention",
            user_id="eval_user",
            query="我从没讨论过面试。",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            memories=[],
            expected_abstain=True,
            demo=True,
        ),
    ]


def test_ablation_runner_reports_every_increment_and_safe_diagnostics() -> None:
    report, outcomes = run_memory_retrieval_ablation(
        embedder=_Embedding(),
        reranker_provider=_Reranker(),
        cases=_cases(),
        include_scale_background=False,
    )

    assert set(report.strategies) == {
        "sql_text",
        "dense_only",
        "bm25_only",
        "dense_bm25_metadata",
        "multi_query",
        "cross_encoder",
        "full_pipeline",
    }
    assert all(len(items) == 2 for items in outcomes.values())
    assert report.held_out_case_count == 0
    assert report.reranker_provider == "deterministic_test"
    assert report.strategies["full_pipeline"].relevant_mrr is not None
    assert (
        report.strategies["full_pipeline"].abstention_precision is not None
    )
    serialized = report.model_dump_json()
    assert "先说一句核心观点" not in serialized
    assert "发言时怎样表达观点" not in serialized


def test_ablation_gate_requires_recall_gain_and_perfect_safety() -> None:
    perfect = EvalMetric(total=1, passed=1, score=1)
    baseline = _report(recall=0.3, safety=perfect)
    candidate = _report(recall=0.5, safety=perfect)
    unsafe = _report(
        recall=0.5,
        safety=EvalMetric(total=1, passed=0, score=0),
    )

    assert passes_ablation_gate(candidate=candidate, baseline=baseline)
    assert not passes_ablation_gate(candidate=unsafe, baseline=baseline)


def _report(*, recall: float, safety: EvalMetric) -> MemoryRetrievalStrategyReport:
    return MemoryRetrievalStrategyReport(
        strategy=MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
        relevant_recall_at_3=EvalMetric(total=1, passed=recall, score=recall),
        false_recall_avoidance=safety,
        stale_recall_avoidance=safety,
        conflict_resolution=safety,
        cross_user_leakage_avoidance=safety,
        no_memory_abstention=safety,
        context_token_budget=safety,
        case_pass_rate=safety,
        p95_query_latency_ms=10,
    )
