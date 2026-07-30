"""Small helpers for deterministic evaluation metrics."""

from app.evals.models import EvalMetric


def ratio(passed: float, total: int) -> EvalMetric:
    """Return a bounded pass ratio, treating empty datasets as zero-score."""
    score = passed / total if total else 0.0
    return EvalMetric(total=total, passed=passed, score=score)


def recall_at_k(retrieved_titles: list[str], expected_titles: list[str], k: int) -> EvalMetric:
    """Return whether any expected title appears in the top-k results."""
    if not expected_titles:
        return ratio(0, 0)
    top_k_titles = set(retrieved_titles[:k])
    expected = set(expected_titles)
    return ratio(1 if top_k_titles.intersection(expected) else 0, 1)


def reciprocal_rank(retrieved_titles: list[str], expected_titles: list[str]) -> float:
    """Return reciprocal rank of the first expected title, or zero if absent."""
    expected = set(expected_titles)
    for index, title in enumerate(retrieved_titles, start=1):
        if title in expected:
            return 1 / index
    return 0.0
