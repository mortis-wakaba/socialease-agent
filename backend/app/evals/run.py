"""Run deterministic project evaluations from bundled JSONL datasets."""

from datetime import datetime, timezone

from app.agents.roleplay import RoleplayAgent
from app.agents.worksheet import WorksheetAgent
from app.evals.loader import (
    load_intent_cases,
    load_rag_cases,
    load_roleplay_feedback_cases,
    load_safety_cases,
    load_worksheet_cases,
)
from app.evals.metrics import ratio
from app.evals.models import EvalReport
from app.knowledge.service import KnowledgeService
from app.models import Intent, RiskLevel, SafetyResult
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


def run_evaluations() -> EvalReport:
    """Execute all bundled deterministic evaluations and return aggregates."""
    safety_classifier = RuleBasedSafetyClassifier()
    intent_router = IntentRouter()
    knowledge_service = KnowledgeService()
    roleplay_agent = RoleplayAgent()
    worksheet_agent = WorksheetAgent()

    safety_cases = load_safety_cases()
    safety_results = [
        _rule_based_safety_for_eval(safety_classifier, case.message) == case.expected_risk_level
        for case in safety_cases
    ]
    crisis_cases = [case for case in safety_cases if case.expected_risk_level == RiskLevel.CRISIS]
    crisis_results = [
        _rule_based_safety_for_eval(safety_classifier, case.message) == RiskLevel.CRISIS
        for case in crisis_cases
    ]

    intent_cases = load_intent_cases()
    intent_results = []
    for case in intent_cases:
        intent_results.append(
            _deterministic_intent_for_eval(intent_router, case.message, case.safety_level)
            == case.expected_intent
        )

    rag_cases = load_rag_cases()
    citation_hits: list[bool] = []
    unknown_correct: list[bool] = []
    for case in rag_cases:
        response = knowledge_service.query(case.query, case.kb_type)
        titles = {citation.title for citation in response.citations}
        if case.expected_titles:
            citation_hits.append(bool(titles.intersection(case.expected_titles)))
        if response.unknown:
            unknown_correct.append(case.expected_unknown)

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
        roleplay_results.append(
            feedback.clarity_score >= case.min_clarity_score
            and feedback.naturalness_score >= case.min_naturalness_score
            and feedback.assertiveness_score >= case.min_assertiveness_score
            and feedback.empathy_score >= case.min_empathy_score
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
        worksheet_results.append(present_ok and missing_ok)

    return EvalReport(
        safety_accuracy=ratio(sum(safety_results), len(safety_results)),
        blocked_crisis_rate=ratio(sum(crisis_results), len(crisis_results)),
        intent_accuracy=ratio(sum(intent_results), len(intent_results)),
        citation_hit_rate=ratio(sum(citation_hits), len(citation_hits)),
        unknown_precision=ratio(sum(unknown_correct), len(unknown_correct)),
        roleplay_feedback_pass_rate=ratio(sum(roleplay_results), len(roleplay_results)),
        worksheet_extraction_pass_rate=ratio(sum(worksheet_results), len(worksheet_results)),
    )


def _deterministic_intent_for_eval(
    intent_router: IntentRouter,
    message: str,
    safety_level: RiskLevel,
) -> Intent:
    """Mirror deterministic routing for the sync eval baseline."""
    if safety_level == RiskLevel.CRISIS:
        return Intent.CRISIS
    scored_matches = intent_router._score_intents(message.casefold())
    return scored_matches[0][0] if scored_matches else Intent.EMOTIONAL_SUPPORT


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
    """Print a JSON evaluation report for CLI use."""
    print(run_evaluations().model_dump_json(indent=2))


if __name__ == "__main__":
    main()
