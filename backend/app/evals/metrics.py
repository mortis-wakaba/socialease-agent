"""Small helpers for deterministic evaluation metrics."""

from app.evals.models import EvalMetric


def ratio(passed: int, total: int) -> EvalMetric:
    """Return a bounded pass ratio, treating empty datasets as zero-score."""
    score = passed / total if total else 0.0
    return EvalMetric(total=total, passed=passed, score=score)
