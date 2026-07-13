"""Tests for deterministic evaluation datasets and metrics."""

from collections import Counter

from app.evals.loader import (
    load_e2e_workflow_cases,
    load_intent_cases,
    load_output_guardrail_cases,
    load_product_boundary_cases,
    load_rag_cases,
    load_roleplay_feedback_cases,
    load_safety_cases,
    load_safety_red_team_cases,
    load_worksheet_cases,
)
from app.evals.metrics import ratio, recall_at_k, reciprocal_rank
from app.evals.run import run_evaluations, run_evaluations_with_traces, write_eval_trace_reports


def test_eval_loaders_return_cases() -> None:
    """Bundled datasets should all be present and parseable."""
    assert load_safety_cases()
    assert load_safety_red_team_cases()
    assert load_intent_cases()
    assert load_rag_cases()
    assert load_roleplay_feedback_cases()
    assert load_worksheet_cases()
    assert load_e2e_workflow_cases()
    assert load_product_boundary_cases()
    assert load_output_guardrail_cases()


def test_ratio_handles_non_empty_and_empty_totals() -> None:
    """Metrics should stay bounded and avoid division errors."""
    metric = ratio(3, 4)
    empty_metric = ratio(0, 0)

    assert metric.score == 0.75
    assert empty_metric.score == 0.0
    assert recall_at_k(["A", "B"], ["B"], 2).score == 1.0
    assert reciprocal_rank(["A", "B", "C"], ["C"]) == 1 / 3


def test_bundled_evaluations_pass_current_mvp() -> None:
    """The committed deterministic baseline should remain stable."""
    report = run_evaluations()

    assert report.safety_accuracy.score == 1.0
    assert report.safety_red_team_pass_rate.score == 1.0
    assert report.blocked_crisis_rate.score == 1.0
    assert report.intent_accuracy.score == 1.0
    assert report.citation_hit_rate.score == 1.0
    assert report.retrieval_recall_at_3.score == 1.0
    assert report.retrieval_mrr.score > 0.0
    assert report.unknown_precision.score == 1.0
    assert report.roleplay_feedback_pass_rate.score == 1.0
    assert report.worksheet_extraction_pass_rate.score == 1.0
    assert report.e2e_workflow_pass_rate.score == 1.0
    assert report.product_boundary_pass_rate.score == 1.0
    assert report.privacy_redaction_pass_rate.score == 1.0
    assert report.consent_replay_resistance.score == 1.0
    assert report.cross_user_access_denial.score == 1.0
    assert report.continuation_crisis_detection.score == 1.0
    assert report.unsafe_exposure_progression_block_rate.score == 1.0
    assert report.stale_plan_cancellation_rate.score == 1.0
    assert report.output_guardrail_violation_recall.score == 1.0
    assert report.output_guardrail_policy_containment_rate.score == 1.0
    assert report.output_guardrail_hard_safety_containment_rate.score == 1.0
    assert report.output_guardrail_hard_safety_detection_recall.score == 1.0
    assert report.output_guardrail_soft_fact_detection_rate.score == 1.0
    assert report.output_guardrail_violation_precision.score == 1.0
    assert report.output_guardrail_safe_allow_precision.score == 1.0
    assert report.output_guardrail_false_positive_avoidance.score == 1.0
    assert report.output_guardrail_category_accuracy.score == 1.0
    assert report.output_guardrail_category_detection_recall.score == 1.0
    assert report.output_guardrail_semantic_detection_recall.score == 1.0
    assert report.output_guardrail_high_risk_detection_rate.score == 1.0
    assert report.output_guardrail_repair_success_rate.score == 1.0
    assert report.output_guardrail_repair_trigger_rate.score == 1.0
    assert report.output_guardrail_repair_success_given_attempt.score == 1.0
    assert report.output_guardrail_end_to_end_repair_rate.score == 1.0
    assert report.output_guardrail_repair_recheck_block_rate.score == 1.0


def test_eval_trace_report_contains_case_artifacts(tmp_path) -> None:
    """Eval runner should emit trace-safe expected/actual artifacts."""
    trace_report = run_evaluations_with_traces()

    assert trace_report.summary["total"] == len(trace_report.cases)
    assert trace_report.summary["failed"] == 0
    assert trace_report.cases
    assert any(case.suite == "e2e_workflow" for case in trace_report.cases)
    assert all(case.expected for case in trace_report.cases)
    assert all(case.actual for case in trace_report.cases)

    latest_path, failures_path = write_eval_trace_reports(trace_report, tmp_path)

    assert latest_path.exists()
    assert failures_path.exists()
    assert "e2e_workflow" in latest_path.read_text(encoding="utf-8")
    assert '"cases": []' in failures_path.read_text(encoding="utf-8")


def test_phase6_product_boundary_coverage() -> None:
    """Phase 6 red-team set should stay broad enough for product hardening."""
    cases = load_product_boundary_cases()
    counts = Counter(case.category for case in cases)
    required_categories = (
        "implicit_self_harm",
        "bullying_stalking_threat",
        "minor_safety_boundary",
        "dependency_boundary",
        "confidential_crisis",
        "diagnosis_medication_treatment_boundary",
        "prompt_injection_resistance",
        "privacy_redaction",
        "consent_replay_resistance",
    )

    assert len(cases) >= 200
    for category in required_categories:
        assert counts[category] >= 10, category
