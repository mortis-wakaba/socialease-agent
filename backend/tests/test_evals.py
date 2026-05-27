"""Tests for deterministic evaluation datasets and metrics."""

from app.evals.loader import (
    load_e2e_workflow_cases,
    load_intent_cases,
    load_rag_cases,
    load_roleplay_feedback_cases,
    load_safety_cases,
    load_safety_red_team_cases,
    load_worksheet_cases,
)
from app.evals.metrics import ratio
from app.evals.run import run_evaluations


def test_eval_loaders_return_cases() -> None:
    """Bundled datasets should all be present and parseable."""
    assert load_safety_cases()
    assert load_safety_red_team_cases()
    assert load_intent_cases()
    assert load_rag_cases()
    assert load_roleplay_feedback_cases()
    assert load_worksheet_cases()
    assert load_e2e_workflow_cases()


def test_ratio_handles_non_empty_and_empty_totals() -> None:
    """Metrics should stay bounded and avoid division errors."""
    metric = ratio(3, 4)
    empty_metric = ratio(0, 0)

    assert metric.score == 0.75
    assert empty_metric.score == 0.0


def test_bundled_evaluations_pass_current_mvp() -> None:
    """The committed deterministic baseline should remain stable."""
    report = run_evaluations()

    assert report.safety_accuracy.score == 1.0
    assert report.safety_red_team_pass_rate.score == 1.0
    assert report.blocked_crisis_rate.score == 1.0
    assert report.intent_accuracy.score == 1.0
    assert report.citation_hit_rate.score == 1.0
    assert report.unknown_precision.score == 1.0
    assert report.roleplay_feedback_pass_rate.score == 1.0
    assert report.worksheet_extraction_pass_rate.score == 1.0
    assert report.e2e_workflow_pass_rate.score == 1.0
