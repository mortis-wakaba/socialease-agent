"""Metric contracts shared by every episodic-memory retrieval strategy."""

from app.evals.memory_retrieval_metrics import (
    build_memory_retrieval_strategy_report,
)
from app.evals.models import (
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalFixture,
)
from app.models_long_term_memory import MemoryType


def test_conflict_metric_accepts_current_target_and_rejects_old_memory() -> None:
    case = MemoryRetrievalEvalCase(
        id="preference_update",
        category="conflict",
        user_id="eval_user",
        query="现在使用关键词，不再准备完整句子。",
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
        memories=[
            MemoryRetrievalFixture(
                memory_id="current",
                user_id="eval_user",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="现在使用三个关键词更自然。",
                occurred_days_ago=1,
            ),
            MemoryRetrievalFixture(
                memory_id="old",
                user_id="eval_user",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="准备完整句子有帮助。",
                occurred_days_ago=30,
            ),
        ],
        expected_memory_ids=["current"],
        forbidden_memory_ids=["old"],
        demo=True,
    )
    report = build_memory_retrieval_strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
        cases=[case],
        outcomes=[
            {
                "case_id": case.id,
                "retrieved_ids": ["current"],
                "estimated_tokens": 10,
                "query_latency_ms": 1.0,
            }
        ],
    )

    assert report.conflict_resolution.total == 1
    assert report.conflict_resolution.score == 1.0
    assert report.case_pass_rate.score == 1.0
