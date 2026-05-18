"""Pydantic models for deterministic evaluation datasets and reports."""

from pydantic import BaseModel, Field

from app.models import Intent, RiskLevel
from app.models_knowledge import KnowledgeBaseType


class SafetyEvalCase(BaseModel):
    """One expected safety-classification example."""

    id: str
    message: str
    expected_risk_level: RiskLevel


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


class EvalMetric(BaseModel):
    """Aggregate result for one evaluation family."""

    total: int
    passed: int
    score: float = Field(ge=0.0, le=1.0)


class EvalReport(BaseModel):
    """Aggregate deterministic evaluation report."""

    safety_accuracy: EvalMetric
    blocked_crisis_rate: EvalMetric
    intent_accuracy: EvalMetric
    citation_hit_rate: EvalMetric
    unknown_precision: EvalMetric
    roleplay_feedback_pass_rate: EvalMetric
    worksheet_extraction_pass_rate: EvalMetric
