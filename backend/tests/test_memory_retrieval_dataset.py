"""Dataset isolation contracts for episodic-memory retrieval evaluations."""

import pytest

from app.evals.memory_retrieval_dataset import (
    MemoryRetrievalEvalSplit,
    build_default_memory_retrieval_dataset,
    validate_memory_retrieval_splits,
)


def test_background_is_restricted_to_one_shared_scale_corpus() -> None:
    dataset = build_default_memory_retrieval_dataset()
    scale_cases = dataset.splits[MemoryRetrievalEvalSplit.SCALE]
    non_scale_cases = [
        *dataset.splits[MemoryRetrievalEvalSplit.DEVELOPMENT],
        *dataset.splits[MemoryRetrievalEvalSplit.HELD_OUT],
    ]

    assert scale_cases
    scale_corpus_ids = {
        record.memory_id
        for record in dataset.records_by_case[scale_cases[0].id]
    }
    assert len(scale_corpus_ids) == 2048 + 108
    assert sum(
        memory_id.startswith("scale_background_")
        for memory_id in scale_corpus_ids
    ) == 2048
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
    assert len(dataset.splits[MemoryRetrievalEvalSplit.HELD_OUT]) == 16
    assert dataset.max_candidates_per_query == 2156
    assert len(dataset.unique_records) > len(dataset.unique_summaries)


def test_split_validator_rejects_query_leakage() -> None:
    dataset = build_default_memory_retrieval_dataset()
    leaked = dataset.splits[MemoryRetrievalEvalSplit.DEVELOPMENT][0].model_copy(
        update={"id": "held_out_leaked_copy"}
    )
    contaminated = {
        split: list(cases) for split, cases in dataset.splits.items()
    }
    contaminated[MemoryRetrievalEvalSplit.HELD_OUT].append(leaked)

    with pytest.raises(ValueError, match="queries overlap"):
        validate_memory_retrieval_splits(contaminated)
