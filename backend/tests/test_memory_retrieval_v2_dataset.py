"""Validation for the held-out memory retrieval v2 dataset."""

from app.evals.loader import load_memory_retrieval_v2_heldout_cases


def test_v2_heldout_dataset_covers_relevance_abstention_and_privacy() -> None:
    cases = load_memory_retrieval_v2_heldout_cases()
    categories = {case.category for case in cases}

    assert len(cases) >= 16
    assert len({case.id for case in cases}) == len(cases)
    assert all(case.demo is True for case in cases)
    assert {
        "semantic_relevance",
        "hard_negative",
        "conflict",
        "cross_user",
        "stale",
        "privacy",
        "injection",
        "safety",
        "abstention",
    }.issubset(categories)
    assert sum(case.expected_abstain for case in cases) >= 7
    assert sum(bool(case.expected_memory_ids) for case in cases) >= 7
