"""Dataset isolation contracts for episodic-memory retrieval evaluations."""

import pytest

from app.evals.memory_retrieval_dataset import (
    MemoryRetrievalEvalSplit,
    build_default_memory_retrieval_dataset,
    validate_memory_retrieval_splits,
)
from app.evals.models import MemoryRetrievalEvalCase, MemoryRetrievalFixture
from app.models_long_term_memory import MemoryType


def test_background_is_restricted_to_one_shared_scale_corpus() -> None:
    dataset = build_default_memory_retrieval_dataset()
    scale_cases = dataset.splits[MemoryRetrievalEvalSplit.SCALE]
    non_scale_cases = [
        *dataset.splits[MemoryRetrievalEvalSplit.DEVELOPMENT],
        *dataset.splits[MemoryRetrievalEvalSplit.VALIDATION],
        *dataset.splits[MemoryRetrievalEvalSplit.SEALED_HELD_OUT],
    ]

    assert scale_cases
    scale_corpus_ids = {
        record.memory_id
        for record in dataset.records_by_case[scale_cases[0].id]
    }
    assert len(scale_corpus_ids) == 2048 * 4 + 36
    assert sum(
        memory_id.startswith("scale_background_")
        for memory_id in scale_corpus_ids
    ) == 2048 * 4
    assert all(
        {
            record.memory_id
            for record in dataset.records_by_case[case.id]
        }
        == scale_corpus_ids
        for case in scale_cases
    )
    assert all(
        len(dataset.records_by_case[case.id]) == len(case.memories)
        for case in non_scale_cases
    )
    assert all(
        not record.memory_id.startswith("scale_background_")
        for case in non_scale_cases
        for record in dataset.records_by_case[case.id]
    )


def test_default_splits_are_exactly_disjoint_and_counts_are_unambiguous() -> None:
    dataset = build_default_memory_retrieval_dataset()

    validate_memory_retrieval_splits(dataset.splits)

    assert len(dataset.cases) == 75
    assert len(dataset.splits[MemoryRetrievalEvalSplit.DEVELOPMENT]) == 23
    assert len(dataset.splits[MemoryRetrievalEvalSplit.SCALE]) == 36
    assert len(dataset.splits[MemoryRetrievalEvalSplit.VALIDATION]) == 16
    assert not dataset.splits[MemoryRetrievalEvalSplit.SEALED_HELD_OUT]
    assert dataset.max_candidates_per_query == 8228
    assert len(dataset.unique_records) == 8304
    assert len(dataset.unique_summaries) == 2160


def test_scale_paraphrases_share_persistent_records_without_equivalent_duplicates() -> None:
    dataset = build_default_memory_retrieval_dataset()
    scale_cases = dataset.splits[MemoryRetrievalEvalSplit.SCALE]
    corpus = dataset.records_by_case[scale_cases[0].id]
    semantic_keys = [
        (
            record.user_id,
            record.memory_type,
            record.summary,
            record.scenario_type,
            record.scenario_id,
            record.practice_thread_id,
            tuple(record.skill_codes),
            tuple(record.context_tags),
            record.status,
            record.occurred_at,
            record.expires_at,
        )
        for record in corpus
    ]

    assert len(semantic_keys) == len(set(semantic_keys))
    for offset in range(0, len(scale_cases), 3):
        paraphrases = scale_cases[offset : offset + 3]
        assert len({tuple(case.expected_memory_ids) for case in paraphrases}) == 1
        assert len({tuple(case.forbidden_memory_ids) for case in paraphrases}) == 1


def test_scale_corpus_exercises_large_multi_user_ownership_boundary() -> None:
    dataset = build_default_memory_retrieval_dataset()
    case = dataset.splits[MemoryRetrievalEvalSplit.SCALE][0]
    corpus = dataset.records_by_case[case.id]
    owners = {record.user_id for record in corpus}
    owner_threads = {
        (record.user_id, record.practice_thread_id)
        for record in corpus
        if record.practice_thread_id is not None
    }

    assert len(owners) == 4
    assert all(
        any(
            owner != case.user_id and thread_id == record.practice_thread_id
            for owner, thread_id in owner_threads
        )
        for record in corpus
        if record.user_id == case.user_id
        and record.practice_thread_id is not None
    )


def test_isolated_boundary_cases_keep_adversaries_without_scale_noise() -> None:
    dataset = build_default_memory_retrieval_dataset()
    boundary_cases = [
        case
        for case in dataset.cases
        if case.category in {"cross_user", "stale", "privacy", "injection", "safety"}
    ]
    cross_user_cases = [
        case for case in boundary_cases if case.category == "cross_user"
    ]

    assert len(cross_user_cases) == 3
    assert all(
        any(
            record.user_id != case.user_id
            for record in dataset.records_by_case[case.id]
        )
        for case in cross_user_cases
    )
    assert all(
        len(dataset.records_by_case[case.id]) == len(case.memories)
        for case in boundary_cases
    )
    assert all(
        not record.memory_id.startswith("scale_background_")
        for case in boundary_cases
        for record in dataset.records_by_case[case.id]
    )


def test_split_validator_rejects_query_leakage() -> None:
    dataset = build_default_memory_retrieval_dataset()
    leaked = dataset.splits[MemoryRetrievalEvalSplit.DEVELOPMENT][0].model_copy(
        update={"id": "held_out_leaked_copy"}
    )
    contaminated = {
        split: list(cases) for split, cases in dataset.splits.items()
    }
    contaminated[MemoryRetrievalEvalSplit.VALIDATION].append(leaked)

    with pytest.raises(ValueError, match="queries overlap"):
        validate_memory_retrieval_splits(contaminated)


def test_case_rejects_indistinguishable_records_with_different_ids() -> None:
    fixture = MemoryRetrievalFixture(
        memory_id="target_a",
        user_id="eval_user",
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary="demo：先写三个关键词。",
        scenario_type="classroom_speech",
        occurred_days_ago=1,
    )

    with pytest.raises(
        ValueError,
        match="indistinguishable memory fixtures",
    ):
        MemoryRetrievalEvalCase(
            id="duplicate_fixture",
            category="semantic_relevance",
            user_id="eval_user",
            query="demo：上次什么方法有帮助？",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            memories=[
                fixture,
                fixture.model_copy(update={"memory_id": "target_b"}),
            ],
            expected_memory_ids=["target_a"],
            demo=True,
        )
