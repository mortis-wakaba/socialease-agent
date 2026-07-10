"""Pydantic models for deterministic evaluation datasets and reports."""

from pydantic import BaseModel, Field

from app.models import Intent, RiskLevel
from app.models_knowledge import KnowledgeBaseType
from datetime import datetime
from typing import Any


class SafetyEvalCase(BaseModel):
    """One expected safety-classification example."""

    id: str
    message: str
    expected_risk_level: RiskLevel


class SafetyRedTeamEvalCase(BaseModel):
    """One red-team safety example for conservative regression checks."""

    id: str
    message: str
    expected_min_risk_level: RiskLevel
    notes: str = ""


class IntentEvalCase(BaseModel):
    """One expected intent-routing example."""

    id: str
    message: str
    expected_intent: Intent
    safety_level: RiskLevel = RiskLevel.LOW


class RagEvalCase(BaseModel):
    """One expected knowledge-retrieval example."""

    id: str
    query: str
    kb_type: KnowledgeBaseType
    expected_unknown: bool
    expected_titles: list[str] = Field(default_factory=list)


class RoleplayFeedbackEvalCase(BaseModel):
    """One expected role-play feedback example."""

    id: str
    scenario: str
    difficulty: int
    user_messages: list[str]
    min_clarity_score: int
    min_naturalness_score: int
    min_assertiveness_score: int
    min_empathy_score: int


class WorksheetEvalCase(BaseModel):
    """One expected worksheet-extraction example."""

    id: str
    message: str
    expected_present_fields: list[str] = Field(default_factory=list)
    expected_missing_fields: list[str] = Field(default_factory=list)


class E2EWorkflowEvalCase(BaseModel):
    """One expected end-to-end harness workflow example."""

    id: str
    message: str
    expected_risk_level: RiskLevel
    expected_intent: Intent
    expected_selected_agent: str
    expected_escalation: bool = False


class ProductBoundaryEvalCase(BaseModel):
    """One expected product-boundary behavior example."""

    id: str
    category: str
    input: dict[str, object] = Field(default_factory=dict)
    expected: dict[str, object] = Field(default_factory=dict)


class EvalMetric(BaseModel):
    """Aggregate result for one evaluation family."""

    total: int
    passed: float
    score: float = Field(ge=0.0, le=1.0)


class EvalReport(BaseModel):
    """Aggregate deterministic evaluation report."""

    safety_accuracy: EvalMetric
    safety_red_team_pass_rate: EvalMetric
    blocked_crisis_rate: EvalMetric
    intent_accuracy: EvalMetric
    citation_hit_rate: EvalMetric
    retrieval_recall_at_3: EvalMetric
    retrieval_mrr: EvalMetric
    unknown_precision: EvalMetric
    roleplay_feedback_pass_rate: EvalMetric
    worksheet_extraction_pass_rate: EvalMetric
    e2e_workflow_pass_rate: EvalMetric
    product_boundary_pass_rate: EvalMetric
    privacy_redaction_pass_rate: EvalMetric
    consent_replay_resistance: EvalMetric
    cross_user_access_denial: EvalMetric
    continuation_crisis_detection: EvalMetric
    unsafe_exposure_progression_block_rate: EvalMetric
    stale_plan_cancellation_rate: EvalMetric


class EvalStepTrace(BaseModel):
    """Expected/actual detail for one deterministic eval step."""

    name: str
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    passed: bool


class EvalCaseTrace(BaseModel):
    """Trace artifact for one deterministic eval case."""

    suite: str
    case_id: str
    category: str | None = None
    passed: bool
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    steps: list[EvalStepTrace] = Field(default_factory=list)
    failure_reason: str | None = None


class EvalTraceReport(BaseModel):
    """Full deterministic eval report plus per-case traces."""

    generated_at: datetime
    report: EvalReport
    summary: dict[str, int]
    cases: list[EvalCaseTrace] = Field(default_factory=list)
