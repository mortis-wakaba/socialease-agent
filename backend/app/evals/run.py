"""Run deterministic project evaluations from bundled JSONL datasets."""

from datetime import datetime, timedelta, timezone
import asyncio
from enum import Enum
from pathlib import Path
from typing import Any
import os
from uuid import uuid4

from pydantic import BaseModel

from app.agents.roleplay import RoleplayAgent
from app.agents.worksheet import WorksheetAgent
from app.evals.loader import (
    load_e2e_workflow_cases,
    load_intent_cases,
    load_memory_retrieval_cases,
    load_output_guardrail_cases,
    load_product_boundary_cases,
    load_rag_cases,
    load_roleplay_feedback_cases,
    load_safety_cases,
    load_safety_red_team_cases,
    load_worksheet_cases,
)
from app.evals.memory_retrieval import run_memory_retrieval_benchmark
from app.evals.metrics import ratio, reciprocal_rank
from app.evals.output_guardrail import (
    evaluate_output_guardrail_cases,
    replay_output_guardrail_factory,
)
from app.evals.models import (
    EvalCaseTrace,
    EvalReport,
    EvalStepTrace,
    EvalTraceReport,
    ProductBoundaryEvalCase,
)
from app.knowledge.service import KnowledgeService
from app.models import Intent, RiskLevel, SafetyResult
from app.safety.actions import HarnessAction
from app.safety.permissions import PermissionAction, SafetyPermissionGate
from app.privacy.persistence_gate import PersistenceGate
from app.privacy.policy import PersistenceKind
from app.protocols.service import ProtocolService
from app.services.retention_service import retention_service
from app.models_knowledge import Citation
from app.models_roleplay import (
    RoleplayGuidance,
    RoleplayMessage,
    RoleplayMessageRole,
    RoleplayScenario,
    RoleplaySession,
)
from app.safety.classifier import RuleBasedSafetyClassifier
from app.workflow.router import IntentRouter
from app.tracing.versions import (
    build_execution_version_info,
    deterministic_eval_dataset_version,
)

REPORTS_DIR = Path(__file__).with_name("reports")


def run_evaluations() -> EvalReport:
    """Execute all bundled deterministic evaluations and return aggregates."""
    return run_evaluations_with_traces().report


def run_evaluations_with_traces() -> EvalTraceReport:
    """Execute deterministic evaluations and return per-case trace artifacts."""
    safety_classifier = RuleBasedSafetyClassifier()
    intent_router = IntentRouter()
    knowledge_service = KnowledgeService()
    roleplay_agent = RoleplayAgent()
    worksheet_agent = WorksheetAgent()
    permission_gate = SafetyPermissionGate()
    case_traces: list[EvalCaseTrace] = []

    memory_retrieval_benchmark, memory_retrieval_outcomes = (
        run_memory_retrieval_benchmark()
    )
    selected_memory_strategy = memory_retrieval_benchmark.strategies[
        memory_retrieval_benchmark.selected_strategy.value
    ]
    memory_cases_by_id = {
        case.id: case for case in load_memory_retrieval_cases()
    }
    for outcome in memory_retrieval_outcomes:
        case = memory_cases_by_id[outcome["case_id"]]
        _append_case_trace(
            case_traces,
            suite="memory_retrieval",
            case_id=case.id,
            category=case.category,
            passed=outcome["passed"],
            expected={
                "memory_ids": case.expected_memory_ids,
                "forbidden_memory_ids": case.forbidden_memory_ids,
                "abstain": case.expected_abstain,
            },
            actual={
                "strategy": memory_retrieval_benchmark.selected_strategy,
                "memory_ids": outcome["retrieved_ids"],
                "eligible_count": outcome["eligible_count"],
                "estimated_tokens": outcome["estimated_tokens"],
            },
            steps=[
                _step_trace(
                    "policy_scoped_retrieval",
                    expected={
                        "memory_ids": case.expected_memory_ids,
                        "forbidden_memory_ids": case.forbidden_memory_ids,
                        "abstain": case.expected_abstain,
                    },
                    actual={
                        "memory_ids": outcome["retrieved_ids"],
                        "eligible_count": outcome["eligible_count"],
                        "estimated_tokens": outcome["estimated_tokens"],
                    },
                    passed=outcome["passed"],
                )
            ],
        )

    safety_cases = load_safety_cases()
    safety_results = []
    for case in safety_cases:
        actual_risk = _rule_based_safety_for_eval(safety_classifier, case.message)
        passed = actual_risk == case.expected_risk_level
        safety_results.append(passed)
        _append_case_trace(
            case_traces,
            suite="safety",
            case_id=case.id,
            passed=passed,
            expected={"risk_level": case.expected_risk_level},
            actual={"risk_level": actual_risk},
            steps=[
                _step_trace(
                    "safety_classifier",
                    expected={"risk_level": case.expected_risk_level},
                    actual={"risk_level": actual_risk},
                    passed=passed,
                )
            ],
        )
    safety_red_team_cases = load_safety_red_team_cases()
    safety_red_team_results = []
    for case in safety_red_team_cases:
        actual_risk = _rule_based_safety_for_eval(safety_classifier, case.message)
        passed = _risk_at_least(actual_risk, case.expected_min_risk_level)
        safety_red_team_results.append(passed)
        _append_case_trace(
            case_traces,
            suite="safety_red_team",
            case_id=case.id,
            passed=passed,
            expected={"min_risk_level": case.expected_min_risk_level, "notes": case.notes},
            actual={"risk_level": actual_risk},
            steps=[
                _step_trace(
                    "safety_floor",
                    expected={"min_risk_level": case.expected_min_risk_level},
                    actual={"risk_level": actual_risk},
                    passed=passed,
                )
            ],
        )
    crisis_cases = [case for case in safety_cases if case.expected_risk_level == RiskLevel.CRISIS]
    crisis_results = []
    for case in crisis_cases:
        actual_risk = _rule_based_safety_for_eval(safety_classifier, case.message)
        passed = actual_risk == RiskLevel.CRISIS
        crisis_results.append(passed)
        _append_case_trace(
            case_traces,
            suite="blocked_crisis",
            case_id=case.id,
            passed=passed,
            expected={"risk_level": RiskLevel.CRISIS},
            actual={"risk_level": actual_risk},
            steps=[
                _step_trace(
                    "crisis_bypass_check",
                    expected={"risk_level": RiskLevel.CRISIS},
                    actual={"risk_level": actual_risk},
                    passed=passed,
                )
            ],
        )

    intent_cases = load_intent_cases()
    intent_results = []
    for case in intent_cases:
        actual_intent = _deterministic_intent_for_eval(
            intent_router,
            case.message,
            case.safety_level,
        )
        passed = actual_intent == case.expected_intent
        intent_results.append(passed)
        _append_case_trace(
            case_traces,
            suite="intent",
            case_id=case.id,
            passed=passed,
            expected={"intent": case.expected_intent, "safety_level": case.safety_level},
            actual={"intent": actual_intent},
            steps=[
                _step_trace(
                    "intent_router",
                    expected={"intent": case.expected_intent},
                    actual={"intent": actual_intent},
                    passed=passed,
                )
            ],
        )

    rag_cases = load_rag_cases()
    citation_hits: list[bool] = []
    recall_at_3_hits: list[bool] = []
    reciprocal_ranks: list[float] = []
    unknown_correct: list[bool] = []
    for case in rag_cases:
        response = knowledge_service.query(case.query, case.kb_type)
        titles = {citation.title for citation in response.citations}
        retrieved_titles = [citation.title for citation in response.citations]
        case_checks: list[bool] = []
        if case.expected_titles:
            citation_hit = bool(titles.intersection(case.expected_titles))
            recall_hit = bool(set(retrieved_titles[:3]).intersection(case.expected_titles))
            rank = reciprocal_rank(retrieved_titles, case.expected_titles)
            citation_hits.append(citation_hit)
            recall_at_3_hits.append(recall_hit)
            reciprocal_ranks.append(rank)
            case_checks.extend([citation_hit, recall_hit, rank > 0])
        if response.unknown:
            unknown_ok = case.expected_unknown
            unknown_correct.append(unknown_ok)
            case_checks.append(unknown_ok)
        elif case.expected_unknown:
            case_checks.append(False)
        else:
            case_checks.append(True)
        passed = all(case_checks)
        _append_case_trace(
            case_traces,
            suite="rag",
            case_id=case.id,
            passed=passed,
            expected={
                "unknown": case.expected_unknown,
                "titles": case.expected_titles,
                "kb_type": case.kb_type,
            },
            actual={
                "unknown": response.unknown,
                "titles": retrieved_titles,
                "confidence": response.confidence,
            },
            steps=[
                _step_trace(
                    "retriever",
                    expected={"titles": case.expected_titles},
                    actual={"titles": retrieved_titles[:3]},
                    passed=not case.expected_titles
                    or bool(set(retrieved_titles[:3]).intersection(case.expected_titles)),
                ),
                _step_trace(
                    "unknown_policy",
                    expected={"unknown": case.expected_unknown},
                    actual={"unknown": response.unknown},
                    passed=response.unknown == case.expected_unknown,
                ),
            ],
        )

    roleplay_cases = load_roleplay_feedback_cases()
    roleplay_results = []
    for case in roleplay_cases:
        now = datetime.now(timezone.utc)
        session = RoleplaySession(
            session_id=f"eval-{case.id}",
            user_id="eval_user",
            scenario=RoleplayScenario(case.scenario),
            difficulty=case.difficulty,
            messages=[
                RoleplayMessage(role=RoleplayMessageRole.USER, content=message, created_at=now)
                for message in case.user_messages
            ],
            retrieved_guidance=RoleplayGuidance(
                query="eval",
                answer="eval",
                citations=[
                    Citation(
                        title="Eval",
                        source_name="Project Authored",
                        source_type="project_authored",
                        snippet="eval",
                    )
                ],
                unknown=False,
                confidence=1.0,
            ),
            created_at=now,
            updated_at=now,
        )
        feedback = roleplay_agent.feedback(session)
        passed = (
            feedback.clarity_score >= case.min_clarity_score
            and feedback.naturalness_score >= case.min_naturalness_score
            and feedback.assertiveness_score >= case.min_assertiveness_score
            and feedback.empathy_score >= case.min_empathy_score
        )
        roleplay_results.append(passed)
        _append_case_trace(
            case_traces,
            suite="roleplay_feedback",
            case_id=case.id,
            passed=passed,
            expected={
                "min_clarity_score": case.min_clarity_score,
                "min_naturalness_score": case.min_naturalness_score,
                "min_assertiveness_score": case.min_assertiveness_score,
                "min_empathy_score": case.min_empathy_score,
            },
            actual={
                "clarity_score": feedback.clarity_score,
                "naturalness_score": feedback.naturalness_score,
                "assertiveness_score": feedback.assertiveness_score,
                "empathy_score": feedback.empathy_score,
            },
            steps=[
                _step_trace(
                    "rubric_scores",
                    expected={
                        "clarity": case.min_clarity_score,
                        "naturalness": case.min_naturalness_score,
                        "assertiveness": case.min_assertiveness_score,
                        "empathy": case.min_empathy_score,
                    },
                    actual={
                        "clarity": feedback.clarity_score,
                        "naturalness": feedback.naturalness_score,
                        "assertiveness": feedback.assertiveness_score,
                        "empathy": feedback.empathy_score,
                    },
                    passed=passed,
                )
            ],
        )

    worksheet_cases = load_worksheet_cases()
    worksheet_results = []
    for case in worksheet_cases:
        fields = worksheet_agent._rule_based_fields(case.message)
        missing_fields = [
            field
            for field in worksheet_agent.required_fields
            if getattr(fields, field) in (None, "")
        ]
        present_ok = all(getattr(fields, field) not in (None, "") for field in case.expected_present_fields)
        missing_ok = all(field in missing_fields for field in case.expected_missing_fields)
        passed = present_ok and missing_ok
        worksheet_results.append(passed)
        actual_present_fields = [
            field
            for field in worksheet_agent.required_fields
            if getattr(fields, field) not in (None, "")
        ]
        _append_case_trace(
            case_traces,
            suite="worksheet",
            case_id=case.id,
            passed=passed,
            expected={
                "present_fields": case.expected_present_fields,
                "missing_fields": case.expected_missing_fields,
            },
            actual={
                "present_fields": actual_present_fields,
                "missing_fields": missing_fields,
            },
            steps=[
                _step_trace(
                    "field_extraction",
                    expected={"present_fields": case.expected_present_fields},
                    actual={"present_fields": actual_present_fields},
                    passed=present_ok,
                ),
                _step_trace(
                    "missing_field_detection",
                    expected={"missing_fields": case.expected_missing_fields},
                    actual={"missing_fields": missing_fields},
                    passed=missing_ok,
                ),
            ],
        )

    e2e_cases = load_e2e_workflow_cases()
    e2e_results = []
    for case in e2e_cases:
        risk_level = _rule_based_safety_for_eval(safety_classifier, case.message)
        safety_result = SafetyResult(risk_level=risk_level, reason="eval")
        crisis_decision = permission_gate.decide(safety_result, HarnessAction.CRISIS_ESCALATION)
        if crisis_decision.action == PermissionAction.ESCALATE:
            intent = Intent.CRISIS
            selected_agent = "crisis_escalation"
            escalation = True
        else:
            intent = _deterministic_intent_for_eval(intent_router, case.message, risk_level)
            decision = permission_gate.decide(safety_result, _action_for_intent(intent))
            selected_agent = (
                "lead_harness"
                if decision.action in {PermissionAction.ASK_CONSENT, PermissionAction.BLOCK}
                else _selected_agent_for_eval(intent)
            )
            escalation = False
        passed = (
            risk_level == case.expected_risk_level
            and intent == case.expected_intent
            and selected_agent == case.expected_selected_agent
            and escalation == case.expected_escalation
        )
        e2e_results.append(passed)
        _append_case_trace(
            case_traces,
            suite="e2e_workflow",
            case_id=case.id,
            passed=passed,
            expected={
                "risk_level": case.expected_risk_level,
                "intent": case.expected_intent,
                "selected_agent": case.expected_selected_agent,
                "escalation": case.expected_escalation,
            },
            actual={
                "risk_level": risk_level,
                "intent": intent,
                "selected_agent": selected_agent,
                "escalation": escalation,
            },
            steps=[
                _step_trace(
                    "safety",
                    expected={"risk_level": case.expected_risk_level},
                    actual={"risk_level": risk_level},
                    passed=risk_level == case.expected_risk_level,
                ),
                _step_trace(
                    "router_or_escalation",
                    expected={"intent": case.expected_intent},
                    actual={"intent": intent},
                    passed=intent == case.expected_intent,
                ),
                _step_trace(
                    "permission_or_skill",
                    expected={"selected_agent": case.expected_selected_agent},
                    actual={"selected_agent": selected_agent},
                    passed=selected_agent == case.expected_selected_agent,
                ),
            ],
        )

    product_boundary_cases = load_product_boundary_cases()
    product_boundary_results_by_category: dict[str, list[bool]] = {}
    for case in product_boundary_cases:
        passed = _evaluate_product_boundary_case(case, safety_classifier)
        product_boundary_results_by_category.setdefault(case.category, []).append(passed)
        _append_case_trace(
            case_traces,
            suite="product_boundary",
            case_id=case.id,
            category=case.category,
            passed=passed,
            expected=case.expected,
            actual={"passed": passed, "category": case.category},
            steps=[
                _step_trace(
                    case.category,
                    expected=case.expected,
                    actual={"passed": passed},
                    passed=passed,
                )
            ],
        )
    all_product_boundary_results = [
        passed
        for results in product_boundary_results_by_category.values()
        for passed in results
    ]
    output_guardrail_evaluation = asyncio.run(
        evaluate_output_guardrail_cases(
            load_output_guardrail_cases(),
            guardrail_factory=replay_output_guardrail_factory,
        )
    )
    for case in output_guardrail_evaluation.cases:
        _append_case_trace(
            case_traces,
            suite="output_guardrail_policy",
            case_id=case.case_id,
            passed=case.passed,
            expected={
                "action": case.expected_action,
                "categories": case.expected_categories,
            },
            actual={
                "action": case.actual_action,
                "categories": case.actual_categories,
            },
            steps=[
                _step_trace(
                    "global_output_policy",
                    expected={
                        "action": case.expected_action,
                        "categories": case.expected_categories,
                    },
                    actual={
                        "action": case.actual_action,
                        "categories": case.actual_categories,
                    },
                    passed=case.passed,
                )
            ],
        )

    report = EvalReport(
        safety_accuracy=ratio(sum(safety_results), len(safety_results)),
        safety_red_team_pass_rate=ratio(sum(safety_red_team_results), len(safety_red_team_results)),
        blocked_crisis_rate=ratio(sum(crisis_results), len(crisis_results)),
        intent_accuracy=ratio(sum(intent_results), len(intent_results)),
        citation_hit_rate=ratio(sum(citation_hits), len(citation_hits)),
        retrieval_recall_at_3=ratio(sum(recall_at_3_hits), len(recall_at_3_hits)),
        retrieval_mrr=ratio(round(sum(reciprocal_ranks), 4), len(reciprocal_ranks)),
        unknown_precision=ratio(sum(unknown_correct), len(unknown_correct)),
        memory_retrieval_recall_at_3=(
            selected_memory_strategy.relevant_recall_at_3
        ),
        memory_false_recall_avoidance=(
            selected_memory_strategy.false_recall_avoidance
        ),
        memory_stale_recall_avoidance=(
            selected_memory_strategy.stale_recall_avoidance
        ),
        memory_conflict_resolution=(
            selected_memory_strategy.conflict_resolution
        ),
        memory_cross_user_leakage_avoidance=(
            selected_memory_strategy.cross_user_leakage_avoidance
        ),
        memory_no_memory_abstention=(
            selected_memory_strategy.no_memory_abstention
        ),
        memory_context_token_budget=(
            selected_memory_strategy.context_token_budget
        ),
        roleplay_feedback_pass_rate=ratio(sum(roleplay_results), len(roleplay_results)),
        worksheet_extraction_pass_rate=ratio(sum(worksheet_results), len(worksheet_results)),
        e2e_workflow_pass_rate=ratio(sum(e2e_results), len(e2e_results)),
        product_boundary_pass_rate=ratio(
            sum(all_product_boundary_results),
            len(all_product_boundary_results),
        ),
        privacy_redaction_pass_rate=_category_metric(
            product_boundary_results_by_category,
            "privacy_redaction",
        ),
        consent_replay_resistance=_category_metric(
            product_boundary_results_by_category,
            "consent_replay_resistance",
        ),
        cross_user_access_denial=_category_metric(
            product_boundary_results_by_category,
            "cross_user_access_denial",
        ),
        continuation_crisis_detection=_category_metric(
            product_boundary_results_by_category,
            "continuation_crisis_detection",
        ),
        unsafe_exposure_progression_block_rate=_category_metric(
            product_boundary_results_by_category,
            "unsafe_exposure_progression",
        ),
        stale_plan_cancellation_rate=_category_metric(
            product_boundary_results_by_category,
            "stale_plan_cancellation",
        ),
        output_guardrail_violation_recall=(
            output_guardrail_evaluation.violation_recall
        ),
        output_guardrail_policy_containment_rate=(
            output_guardrail_evaluation.policy_containment_rate
        ),
        output_guardrail_hard_safety_containment_rate=(
            output_guardrail_evaluation.hard_safety_containment_rate
        ),
        output_guardrail_hard_safety_detection_recall=(
            output_guardrail_evaluation.hard_safety_detection_recall
        ),
        output_guardrail_soft_fact_detection_rate=(
            output_guardrail_evaluation.soft_fact_detection_rate
        ),
        output_guardrail_violation_precision=(
            output_guardrail_evaluation.violation_precision
        ),
        output_guardrail_safe_allow_precision=(
            output_guardrail_evaluation.safe_allow_precision
        ),
        output_guardrail_false_positive_avoidance=(
            output_guardrail_evaluation.false_positive_avoidance
        ),
        output_guardrail_category_accuracy=(
            output_guardrail_evaluation.category_accuracy
        ),
        output_guardrail_category_detection_recall=(
            output_guardrail_evaluation.category_detection_recall
        ),
        output_guardrail_semantic_detection_recall=(
            output_guardrail_evaluation.semantic_detection_recall
        ),
        output_guardrail_high_risk_detection_rate=(
            output_guardrail_evaluation.high_risk_detection_rate
        ),
        output_guardrail_repair_success_rate=(
            output_guardrail_evaluation.repair_success_rate
        ),
        output_guardrail_repair_trigger_rate=(
            output_guardrail_evaluation.repair_trigger_rate
        ),
        output_guardrail_repair_success_given_attempt=(
            output_guardrail_evaluation.repair_success_given_attempt
        ),
        output_guardrail_end_to_end_repair_rate=(
            output_guardrail_evaluation.end_to_end_repair_rate
        ),
        output_guardrail_repair_recheck_block_rate=(
            output_guardrail_evaluation.repair_recheck_block_rate
        ),
    )
    passed_cases = sum(1 for case in case_traces if case.passed)
    return EvalTraceReport(
        generated_at=datetime.now(timezone.utc),
        execution_version=build_execution_version_info(
            eval_dataset_version=deterministic_eval_dataset_version(),
        ),
        report=report,
        summary={
            "total": len(case_traces),
            "passed": passed_cases,
            "failed": len(case_traces) - passed_cases,
        },
        cases=case_traces,
    )


def _deterministic_intent_for_eval(
    intent_router: IntentRouter,
    message: str,
    safety_level: RiskLevel,
) -> Intent:
    """Mirror deterministic routing for the sync eval baseline."""
    if safety_level == RiskLevel.CRISIS:
        return Intent.CRISIS
    if intent_router._is_stop_practice_request(message.casefold()):
        return Intent.EMOTIONAL_SUPPORT
    scored_matches = intent_router._score_intents(message.casefold())
    return scored_matches[0][0] if scored_matches else Intent.EMOTIONAL_SUPPORT


def _selected_agent_for_eval(intent: Intent) -> str:
    """Mirror executable skill dispatch for deterministic E2E evals."""
    return {
        Intent.EMOTIONAL_SUPPORT: "support_agent",
        Intent.ROLEPLAY_PRACTICE: "roleplay_agent",
        Intent.CBT_WORKSHEET: "worksheet_agent",
        Intent.EXPOSURE_PLANNING: "exposure_planner",
        Intent.PROGRESS_REVIEW: "exposure_planner",
        Intent.CAMPUS_RESOURCE_QUERY: "support_resource_rag_agent",
        Intent.CALENDAR_PLANNING: "calendar_planner",
        Intent.CLARIFICATION_NEEDED: "clarification_agent",
        Intent.OUT_OF_SCOPE: "product_boundary_agent",
        Intent.CRISIS: "crisis_escalation",
    }.get(intent, "support_agent")


def _action_for_intent(intent: Intent) -> HarnessAction:
    """Mirror intent-to-action mapping used by the harness."""
    return {
        Intent.EMOTIONAL_SUPPORT: HarnessAction.GENERAL_SUPPORT,
        Intent.ROLEPLAY_PRACTICE: HarnessAction.START_ROLEPLAY,
        Intent.CBT_WORKSHEET: HarnessAction.CREATE_WORKSHEET,
        Intent.EXPOSURE_PLANNING: HarnessAction.CREATE_EXPOSURE_PLAN,
        Intent.PROGRESS_REVIEW: HarnessAction.CREATE_EXPOSURE_PLAN,
        Intent.CAMPUS_RESOURCE_QUERY: HarnessAction.QUERY_SUPPORT_RESOURCE,
        Intent.CALENDAR_PLANNING: HarnessAction.PROPOSE_CALENDAR_EVENT,
        Intent.CLARIFICATION_NEEDED: HarnessAction.REQUEST_CLARIFICATION,
        Intent.OUT_OF_SCOPE: HarnessAction.DECLINE_OUT_OF_SCOPE,
        Intent.CRISIS: HarnessAction.CRISIS_ESCALATION,
    }.get(intent, HarnessAction.GENERAL_SUPPORT)


def _risk_at_least(actual: RiskLevel, minimum: RiskLevel) -> bool:
    """Return whether the actual risk is at least the expected minimum."""
    rank = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRISIS: 3,
    }
    return rank[actual] >= rank[minimum]


def _category_metric(results_by_category: dict[str, list[bool]], category: str):
    """Return an EvalMetric for a product-boundary category."""
    results = results_by_category.get(category, [])
    return ratio(sum(results), len(results))


def write_eval_trace_reports(
    trace_report: EvalTraceReport,
    reports_dir: Path = REPORTS_DIR,
) -> tuple[Path, Path]:
    """Write full and failed-case eval trace artifacts to disk."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    latest_path = reports_dir / "latest.json"
    failures_path = reports_dir / "latest_failures.json"
    latest_path.write_text(
        trace_report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    failures = trace_report.model_copy(
        update={
            "summary": {
                "total": trace_report.summary["total"],
                "passed": trace_report.summary["passed"],
                "failed": trace_report.summary["failed"],
            },
            "cases": [case for case in trace_report.cases if not case.passed],
        }
    )
    failures_path.write_text(
        failures.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return latest_path, failures_path


def _append_case_trace(
    case_traces: list[EvalCaseTrace],
    *,
    suite: str,
    case_id: str,
    passed: bool,
    expected: dict[str, Any],
    actual: dict[str, Any],
    steps: list[EvalStepTrace],
    category: str | None = None,
) -> None:
    """Append one trace-safe expected/actual eval artifact."""
    failed_steps = [step.name for step in steps if not step.passed]
    case_traces.append(
        EvalCaseTrace(
            suite=suite,
            case_id=case_id,
            category=category,
            passed=passed,
            expected=_jsonable_dict(expected),
            actual=_jsonable_dict(actual),
            steps=steps,
            failure_reason=(
                None
                if passed
                else f"Failed steps: {', '.join(failed_steps) or 'case_result'}"
            ),
        )
    )


def _step_trace(
    name: str,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    passed: bool,
) -> EvalStepTrace:
    """Build one normalized eval step trace."""
    return EvalStepTrace(
        name=name,
        expected=_jsonable_dict(expected),
        actual=_jsonable_dict(actual),
        passed=passed,
    )


def _jsonable_dict(value: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe dict for eval trace artifacts."""
    return {key: _jsonable(item) for key, item in value.items()}


def _jsonable(value: Any) -> Any:
    """Normalize Pydantic models and enums for report JSON."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _evaluate_product_boundary_case(
    case: ProductBoundaryEvalCase,
    safety_classifier: RuleBasedSafetyClassifier,
) -> bool:
    """Evaluate one deterministic product-boundary case."""
    if case.category == "privacy_redaction":
        return _eval_privacy_redaction(case)
    if case.category in {
        "safety_floor",
        "implicit_self_harm",
        "bullying_stalking_threat",
        "prompt_injection_resistance",
        "confidential_crisis",
        "minor_safety_boundary",
    }:
        return _eval_safety_floor(case, safety_classifier)
    if case.category in {
        "medical_boundary",
        "diagnosis_medication_treatment_boundary",
        "therapist_impersonation",
        "over_medical_advice",
        "dependency_boundary",
    }:
        return _eval_response_boundary(case)
    if case.category == "consent_replay_resistance":
        return _eval_consent_replay(case)
    if case.category == "cross_user_access_denial":
        return _eval_cross_user_access(case)
    if case.category == "continuation_crisis_detection":
        reflection = str(case.input.get("reflection", ""))
        return _rule_based_safety_for_eval(safety_classifier, reflection).value == case.expected.get(
            "risk_level"
        )
    if case.category == "unsafe_exposure_progression":
        reflection = str(case.input.get("reflection", ""))
        risk = _rule_based_safety_for_eval(safety_classifier, reflection)
        return risk == RiskLevel.CRISIS and case.expected.get("blocked") is True
    if case.category == "stale_plan_cancellation":
        return _eval_stale_plan_cancellation(case)
    if case.category == "stop_practice_handling":
        return _eval_stop_practice_handling(case)
    return False


def _eval_safety_floor(
    case: ProductBoundaryEvalCase,
    safety_classifier: RuleBasedSafetyClassifier,
) -> bool:
    """Evaluate safety-floor behavior for product-boundary cases."""
    text = str(case.input.get("text", ""))
    actual = _rule_based_safety_for_eval(safety_classifier, text)
    expected_exact = case.expected.get("risk_level")
    if isinstance(expected_exact, str):
        return actual.value == expected_exact
    expected_min = case.expected.get("min_risk_level")
    if isinstance(expected_min, str):
        return _risk_at_least(actual, RiskLevel(expected_min))
    return False


def _eval_response_boundary(case: ProductBoundaryEvalCase) -> bool:
    """Evaluate static non-medical response boundary text."""
    response_type = str(case.input.get("response_type", "support"))
    if response_type == "crisis":
        from app.safety.crisis import full_crisis_escalation_response

        response = full_crisis_escalation_response()
    else:
        from app.agents.support import SupportAgent

        response, _ = SupportAgent().respond(
            message=str(case.input.get("text", "")),
            intent=Intent.EMOTIONAL_SUPPORT,
            safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="eval"),
        )
    forbidden = [str(item) for item in case.expected.get("forbidden_phrases", [])]
    required = [str(item) for item in case.expected.get("required_phrases", [])]
    return all(item not in response for item in forbidden) and all(
        item in response for item in required
    )


def _eval_privacy_redaction(case: ProductBoundaryEvalCase) -> bool:
    """Evaluate privacy persistence-gate behavior."""
    gate = PersistenceGate()
    text = str(case.input.get("text", ""))
    kind = PersistenceKind(str(case.input.get("kind", "trace_input")))
    previous_trace_output_mode = os.environ.get("SOCIALEASE_TRACE_OUTPUT_MODE")
    configured_trace_output_mode = case.input.get("trace_output_mode")
    if isinstance(configured_trace_output_mode, str):
        os.environ["SOCIALEASE_TRACE_OUTPUT_MODE"] = configured_trace_output_mode
    try:
        decision = gate.persist_text(
            user_id=f"eval_privacy_{uuid4().hex}",
            kind=kind,
            text=text,
        )
    finally:
        if isinstance(configured_trace_output_mode, str):
            if previous_trace_output_mode is None:
                os.environ.pop("SOCIALEASE_TRACE_OUTPUT_MODE", None)
            else:
                os.environ["SOCIALEASE_TRACE_OUTPUT_MODE"] = previous_trace_output_mode
    expected_types = case.expected.get("redacted_types", [])
    not_contains = str(case.expected.get("not_contains", ""))
    passed = (
        decision.minimized is case.expected.get("minimized")
        and all(item in decision.redacted_types for item in expected_types)
        and not_contains not in decision.persisted_text
    )
    if "summarized" in case.expected:
        passed = passed and decision.summarized is case.expected.get("summarized")
    expected_policy = case.expected.get("policy")
    if isinstance(expected_policy, str):
        passed = passed and decision.policy == expected_policy
    return passed


def _eval_consent_replay(case: ProductBoundaryEvalCase) -> bool:
    """Evaluate that consent can be consumed only once."""
    service = ProtocolService()
    user_id = f"eval_consent_{uuid4().hex}"
    request_hash = str(case.input.get("request_hash", "eval-hash"))
    harness_action = HarnessAction(str(case.input.get("harness_action", "start_roleplay")))
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=harness_action,
        reason="eval",
        required_protocol="eval_consent",
        session_id=None,
        request_hash=request_hash,
    )
    service.respond(protocol_id=protocol.protocol_id, user_id=user_id, approved=True)
    first = service.consume_for_action(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        harness_action=harness_action,
        request_hash=request_hash,
        session_id=None,
    )
    second = service.consume_for_action(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        harness_action=harness_action,
        request_hash=request_hash,
        session_id=None,
    )
    return (first is not None) is case.expected.get("first_consume") and (
        second is not None
    ) is case.expected.get("second_consume")


def _eval_cross_user_access(case: ProductBoundaryEvalCase) -> bool:
    """Evaluate owner-scoped protocol access semantics."""
    service = ProtocolService()
    owner = f"{case.input.get('owner', 'owner')}_{uuid4().hex}"
    other = f"{case.input.get('other', 'other')}_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=owner,
        harness_action=HarnessAction.START_ROLEPLAY,
        reason="eval",
        required_protocol="eval_consent",
        session_id=None,
        request_hash="cross-user-hash",
    )
    other_record = service.store.get_for_user(protocol.protocol_id, other)
    return (other_record is not None) is case.expected.get("other_can_access")


def _eval_stale_plan_cancellation(case: ProductBoundaryEvalCase) -> bool:
    """Evaluate abandoned plan cleanup path at the retention-service boundary."""
    from app.memory.intervention_plan_store import intervention_plan_store
    from app.models_intervention import InterventionStep

    user_id = f"eval_stale_plan_{uuid4().hex}"
    plan = intervention_plan_store.create(
        user_id=user_id,
        session_id=f"eval-session-{uuid4().hex}",
        status="pending_consent",
        protocol_id=None,
        steps=[
            InterventionStep(
                step_id="eval-consent",
                title="eval consent",
                status="in_progress",
                skill="lead_harness",
                requires_consent=True,
            )
        ],
    )
    plan = intervention_plan_store.save(
        plan.model_copy(update={"updated_at": datetime.now(timezone.utc) - timedelta(hours=2)})
    )
    cancelled = retention_service.cancel_abandoned_intervention_plans(
        older_than_minutes=int(case.input.get("older_than_minutes", 60)),
        now=datetime.now(timezone.utc),
    )
    updated = intervention_plan_store.get_by_id_for_user(plan.plan_id, user_id)
    return (
        cancelled >= 1
        and updated is not None
        and updated.status == "cancelled"
    ) is case.expected.get("cancelled")


def _eval_stop_practice_handling(case: ProductBoundaryEvalCase) -> bool:
    """Evaluate that stop/pause requests are not routed into active practice."""
    router = IntentRouter()
    text = str(case.input.get("text", ""))
    expected_intent = case.expected.get("intent")
    actual_intent = _deterministic_intent_for_eval(router, text, RiskLevel.LOW)
    return actual_intent.value == expected_intent


def _rule_based_safety_for_eval(
    safety_classifier: RuleBasedSafetyClassifier,
    message: str,
) -> RiskLevel:
    """Mirror sync deterministic safety classification for eval baselines."""
    normalized = message.casefold()
    if safety_classifier._first_match(normalized, safety_classifier.crisis_terms):
        return RiskLevel.CRISIS
    if safety_classifier._first_match(normalized, safety_classifier.high_terms):
        return RiskLevel.HIGH
    if safety_classifier._first_match(normalized, safety_classifier.medium_terms):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def main() -> None:
    """Print aggregate metrics and write per-case trace artifacts."""
    trace_report = run_evaluations_with_traces()
    write_eval_trace_reports(trace_report)
    print(trace_report.report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
