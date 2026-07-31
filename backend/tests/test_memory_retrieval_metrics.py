"""Metric contracts shared by every episodic-memory retrieval strategy."""

import pytest
from pydantic import ValidationError

from app.evals.memory_retrieval_metrics import (
    build_memory_retrieval_strategy_report,
)
from app.evals.models import (
    MemoryRetrievalBenchmarkStrategy,
    MemoryRetrievalEvalCase,
    MemoryRetrievalFixture,
)
from app.models_long_term_memory import MemoryRecordStatus, MemoryType


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


def test_recall_is_query_averaged_and_item_false_recall_is_separate() -> None:
    cases = [
        MemoryRetrievalEvalCase(
            id="two_expected",
            category="multiple_relevant",
            user_id="eval_user",
            query="有哪些旧方法？",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            memories=[
                MemoryRetrievalFixture(
                    memory_id=memory_id,
                    user_id="eval_user",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary=f"demo {memory_id}",
                    occurred_days_ago=1,
                )
                for memory_id in ("a", "b", "bad")
            ],
            expected_memory_ids=["a", "b"],
            forbidden_memory_ids=["bad"],
            demo=True,
        ),
        MemoryRetrievalEvalCase(
            id="one_expected",
            category="semantic_relevance",
            user_id="eval_user",
            query="另一个旧方法？",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            memories=[
                MemoryRetrievalFixture(
                    memory_id="c",
                    user_id="eval_user",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary="demo c",
                    occurred_days_ago=1,
                )
            ],
            expected_memory_ids=["c"],
            demo=True,
        ),
    ]
    report = build_memory_retrieval_strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
        cases=cases,
        outcomes=[
            {
                "case_id": "two_expected",
                "retrieved_ids": ["a", "bad"],
                "estimated_tokens": 10,
            },
            {
                "case_id": "one_expected",
                "retrieved_ids": [],
                "estimated_tokens": 0,
            },
        ],
    )

    assert report.relevant_recall_at_3.score == 0.25
    assert report.relevant_hit_at_3.score == 0.5
    assert report.all_relevant_recall_at_3.score == 0.0
    assert report.forbidden_item_avoidance.score == 0.0
    assert report.judged_item_precision_at_3.score == 0.5


def test_equivalent_relevance_group_accepts_any_member_without_double_counting() -> None:
    case = MemoryRetrievalEvalCase(
        id="equivalent_summary",
        category="multiple_representations",
        user_id="eval_user",
        query="上次发言时什么方法有帮助？",
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
        memories=[
            MemoryRetrievalFixture(
                memory_id="raw_event",
                user_id="eval_user",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="上次先写三个关键词后完成了发言。",
                occurred_days_ago=2,
            ),
            MemoryRetrievalFixture(
                memory_id="equivalent_summary",
                user_id="eval_user",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="三个关键词曾帮助完成发言。",
                occurred_days_ago=1,
            ),
        ],
        expected_memory_ids=["raw_event", "equivalent_summary"],
        relevance_groups=[["raw_event", "equivalent_summary"]],
        demo=True,
    )

    report = build_memory_retrieval_strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.BM25_ONLY,
        cases=[case],
        outcomes=[
            {
                "case_id": case.id,
                "retrieved_ids": ["equivalent_summary"],
                "estimated_tokens": 10,
            }
        ],
    )

    assert report.relevant_recall_at_3.score == 1.0
    assert report.relevant_hit_at_3.score == 1.0
    assert report.all_relevant_recall_at_3.score == 1.0
    assert report.case_pass_rate.score == 1.0


def test_relevance_groups_must_partition_expected_ids() -> None:
    with pytest.raises(
        ValidationError,
        match="relevance groups must partition expected memory ids",
    ):
        MemoryRetrievalEvalCase(
            id="bad_equivalence",
            category="multiple_representations",
            user_id="eval_user",
            query="demo query",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            memories=[
                MemoryRetrievalFixture(
                    memory_id="a",
                    user_id="eval_user",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary="demo a",
                    occurred_days_ago=1,
                ),
                MemoryRetrievalFixture(
                    memory_id="b",
                    user_id="eval_user",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary="demo b",
                    occurred_days_ago=1,
                ),
            ],
            expected_memory_ids=["a", "b"],
            relevance_groups=[["a"]],
            demo=True,
        )


def test_sealed_category_aliases_and_fixture_scope_drive_safety_metrics() -> None:
    conflict = MemoryRetrievalEvalCase(
        id="sealed_conflict",
        category="conflict_or_supersession",
        user_id="eval_user",
        query="demo current preference",
        allowed_memory_types=[MemoryType.RECURRING_PATTERN],
        memories=[
            MemoryRetrievalFixture(
                memory_id="current",
                user_id="eval_user",
                memory_type=MemoryType.RECURRING_PATTERN,
                summary="demo current",
                occurred_days_ago=1,
            ),
            MemoryRetrievalFixture(
                memory_id="superseded",
                user_id="eval_user",
                memory_type=MemoryType.RECURRING_PATTERN,
                summary="demo superseded",
                status=MemoryRecordStatus.SUPERSEDED,
                occurred_days_ago=30,
            ),
        ],
        expected_memory_ids=["current"],
        forbidden_memory_ids=["superseded"],
        demo=True,
    )
    ownership = MemoryRetrievalEvalCase(
        id="sealed_ownership",
        category="ownership_or_lifecycle",
        user_id="eval_user",
        query="demo owned memory",
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
        memories=[
            MemoryRetrievalFixture(
                memory_id="owned",
                user_id="eval_user",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="demo owned",
                occurred_days_ago=1,
            ),
            MemoryRetrievalFixture(
                memory_id="foreign",
                user_id="other_user",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="demo foreign",
                occurred_days_ago=1,
            ),
            MemoryRetrievalFixture(
                memory_id="expired",
                user_id="eval_user",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="demo expired",
                occurred_days_ago=30,
                expires_days_from_now=-1,
            ),
        ],
        expected_memory_ids=["owned"],
        forbidden_memory_ids=["foreign", "expired"],
        demo=True,
    )
    report = build_memory_retrieval_strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.FULL_PIPELINE,
        cases=[conflict, ownership],
        outcomes=[
            {
                "case_id": conflict.id,
                "retrieved_ids": ["current"],
                "estimated_tokens": 5,
            },
            {
                "case_id": ownership.id,
                "retrieved_ids": ["owned"],
                "estimated_tokens": 5,
            },
        ],
    )

    assert report.conflict_resolution.total == 1
    assert report.conflict_resolution.score == 1.0
    assert report.cross_user_leakage_avoidance.total == 1
    assert report.cross_user_leakage_avoidance.score == 1.0
    assert report.stale_recall_avoidance.total == 2
    assert report.stale_recall_avoidance.score == 1.0


def test_latency_report_exposes_sample_count_and_tail_percentiles() -> None:
    cases: list[MemoryRetrievalEvalCase] = []
    outcomes: list[dict[str, object]] = []
    for index, latency in enumerate((1.0, 2.0, 3.0, 100.0)):
        memory_id = f"target_{index}"
        case = MemoryRetrievalEvalCase(
            id=f"latency_{index}",
            category="semantic_relevance",
            user_id="eval_user",
            query=f"demo latency query {index}",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            memories=[
                MemoryRetrievalFixture(
                    memory_id=memory_id,
                    user_id="eval_user",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary=f"demo latency target {index}",
                    occurred_days_ago=index,
                )
            ],
            expected_memory_ids=[memory_id],
            demo=True,
        )
        cases.append(case)
        outcomes.append(
            {
                "case_id": case.id,
                "retrieved_ids": [memory_id],
                "estimated_tokens": 5,
                "query_latency_ms": latency,
            }
        )

    report = build_memory_retrieval_strategy_report(
        strategy=MemoryRetrievalBenchmarkStrategy.BM25_ONLY,
        cases=cases,
        outcomes=outcomes,
    )

    assert report.latency_sample_count == 4
    assert report.mean_query_latency_ms == 26.5
    assert report.p50_query_latency_ms == 2.0
    assert report.p95_query_latency_ms == 100.0
    assert report.p99_query_latency_ms == 100.0
