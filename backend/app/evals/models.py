"""Pydantic models for deterministic evaluation datasets and reports."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models_knowledge import KnowledgeBaseType
from app.models import Intent, RiskLevel
from app.models_long_term_memory import (
    MemoryRecordStatus,
    MemoryType,
)
from app.models_trace import ExecutionVersionInfo
from app.models_scenario import SocialSkillCode


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


class MemoryRetrievalFixture(BaseModel):
    """One synthetic durable record used only by deterministic retrieval evals."""

    memory_id: str
    user_id: str
    memory_type: MemoryType
    summary: str
    scenario_type: str | None = None
    scenario_id: str | None = None
    practice_thread_id: str | None = None
    skill_codes: list[SocialSkillCode] = Field(default_factory=list, max_length=5)
    context_tags: list[str] = Field(default_factory=list, max_length=5)
    status: MemoryRecordStatus = MemoryRecordStatus.ACTIVE
    occurred_days_ago: int = Field(ge=0, le=730)
    expires_days_from_now: int | None = Field(default=180, ge=-730, le=730)
    source_id: str | None = None
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)


class MemoryRetrievalEvalCase(BaseModel):
    """One fixed Chinese memory-retrieval expectation."""

    id: str
    category: str
    user_id: str
    query: str
    scenario_type: str | None = None
    scenario_id: str | None = None
    practice_thread_id: str | None = None
    skill_codes: list[SocialSkillCode] = Field(default_factory=list, max_length=5)
    allowed_memory_types: list[MemoryType] = Field(min_length=1, max_length=5)
    include_archived: bool = False
    memories: list[MemoryRetrievalFixture] = Field(default_factory=list)
    expected_memory_ids: list[str] = Field(default_factory=list)
    forbidden_memory_ids: list[str] = Field(default_factory=list)
    expected_abstain: bool = False
    demo: Literal[True]

    @model_validator(mode="after")
    def validate_retrieval_labels(self) -> "MemoryRetrievalEvalCase":
        """Reject ambiguous labels before they can contaminate metrics."""
        memory_ids = [memory.memory_id for memory in self.memories]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("memory fixture ids must be unique within a case")
        if len(self.expected_memory_ids) != len(set(self.expected_memory_ids)):
            raise ValueError("expected memory ids must be unique")
        if len(self.forbidden_memory_ids) != len(set(self.forbidden_memory_ids)):
            raise ValueError("forbidden memory ids must be unique")
        expected = set(self.expected_memory_ids)
        forbidden = set(self.forbidden_memory_ids)
        available = set(memory_ids)
        if expected.intersection(forbidden):
            raise ValueError("expected and forbidden memory ids must be disjoint")
        if not expected.issubset(available) or not forbidden.issubset(available):
            raise ValueError("retrieval labels must reference case fixtures")
        if self.expected_abstain and expected:
            raise ValueError("abstention cases cannot contain expected memory ids")
        if not self.expected_abstain and not expected:
            raise ValueError("each case must expect memories or explicit abstention")
        return self


class MemoryRetrievalScaleSeed(BaseModel):
    """Compact human-authored seed expanded into scale retrieval cases."""

    id: str
    scenario_type: str
    queries: list[str] = Field(min_length=3, max_length=3)
    target_summary: str
    hard_negative_summaries: list[str] = Field(min_length=2, max_length=4)
    demo: Literal[True]


class MemoryRetrievalBenchmarkStrategy(str, Enum):
    """Offline retrieval variants compared before production adoption."""

    RECENT = "recent"
    METADATA = "metadata"
    SQL_TEXT = "sql_text"
    VECTOR = "vector"
    HYBRID = "hybrid"
    DENSE_ONLY = "dense_only"
    BM25_ONLY = "bm25_only"
    MULTI_ROUTE = "dense_bm25_metadata"
    MULTI_QUERY = "multi_query"
    CROSS_ENCODER = "cross_encoder"
    FULL_PIPELINE = "full_pipeline"


class MemoryRetrievalStrategyReport(BaseModel):
    """Comparable deterministic metrics for one retrieval baseline."""

    strategy: MemoryRetrievalBenchmarkStrategy
    relevant_recall_at_3: "EvalMetric"
    false_recall_avoidance: "EvalMetric"
    stale_recall_avoidance: "EvalMetric"
    conflict_resolution: "EvalMetric"
    cross_user_leakage_avoidance: "EvalMetric"
    no_memory_abstention: "EvalMetric"
    context_token_budget: "EvalMetric"
    case_pass_rate: "EvalMetric"
    relevant_mrr: "EvalMetric | None" = None
    abstention_precision: "EvalMetric | None" = None
    abstention_recall: "EvalMetric | None" = None
    mean_query_latency_ms: float = Field(default=0.0, ge=0.0)
    p95_query_latency_ms: float = Field(default=0.0, ge=0.0)


class MemoryRetrievalBenchmarkReport(BaseModel):
    """A/B/C comparison and explicit vector-adoption decision."""

    selected_strategy: MemoryRetrievalBenchmarkStrategy
    strategies: dict[str, MemoryRetrievalStrategyReport]
    dataset_case_count: int = Field(default=0, ge=0)
    embedder_provider: str | None = None
    embedding_model: str | None = None
    embedding_model_revision: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1)
    model_size_mb: float | None = Field(default=None, ge=0.0)
    cold_start_latency_ms: float | None = Field(default=None, ge=0.0)
    indexed_memory_count: int | None = Field(default=None, ge=0)
    max_candidates_per_query: int | None = Field(default=None, ge=0)
    classical_candidate_window: int | None = Field(default=None, ge=1)
    estimated_index_bytes: int | None = Field(default=None, ge=0)
    document_embedding_latency_ms: float | None = Field(default=None, ge=0.0)
    vector_evaluated: bool = False
    hybrid_evaluated: bool = False
    scale_gate_met: bool = False
    vector_gate_met: bool = False


class MemoryRetrievalAblationReport(BaseModel):
    """Comparable v2 ablations and an explicit production adoption decision."""

    selected_strategy: MemoryRetrievalBenchmarkStrategy
    baseline_strategy: MemoryRetrievalBenchmarkStrategy
    strategies: dict[str, MemoryRetrievalStrategyReport]
    dataset_case_count: int = Field(ge=0)
    development_case_count: int = Field(default=0, ge=0)
    scale_case_count: int = Field(default=0, ge=0)
    held_out_case_count: int = Field(ge=0)
    indexed_memory_count: int = Field(ge=0)
    unique_summary_count: int = Field(default=0, ge=0)
    max_candidates_per_query: int = Field(default=0, ge=0)
    document_embedding_latency_ms: float = Field(default=0.0, ge=0.0)
    reranker_provider: str
    reranker_model: str
    splits: dict[str, "MemoryRetrievalSplitReport"] = Field(default_factory=dict)
    adoption_gate_met: bool


class MemoryRetrievalSplitReport(BaseModel):
    """Strategy metrics for one non-overlapping evaluation split."""

    case_count: int = Field(ge=0)
    max_candidates_per_query: int = Field(ge=0)
    strategies: dict[str, MemoryRetrievalStrategyReport]


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


class OutputGuardrailEvalCase(BaseModel):
    """One demo-only final-output allow/replace expectation."""

    id: str
    user_message: str
    response: str
    intent: Intent
    risk_level: RiskLevel
    selected_skill: str
    selected_agent: str
    expected_action: Literal["allow", "repair", "replace"]
    expected_categories: list[str] = Field(default_factory=list)
    semantic_violations: list[dict[str, str]] = Field(default_factory=list)
    expected_repaired_response: str | None = None
    repair_recheck_violations: list[dict[str, str]] = Field(default_factory=list)
    grounding_metadata: dict[str, object] | None = None
    demo: Literal[True]


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
    memory_retrieval_recall_at_3: EvalMetric
    memory_false_recall_avoidance: EvalMetric
    memory_stale_recall_avoidance: EvalMetric
    memory_conflict_resolution: EvalMetric
    memory_cross_user_leakage_avoidance: EvalMetric
    memory_no_memory_abstention: EvalMetric
    memory_context_token_budget: EvalMetric
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
    output_guardrail_violation_recall: EvalMetric
    output_guardrail_policy_containment_rate: EvalMetric
    output_guardrail_hard_safety_containment_rate: EvalMetric
    output_guardrail_hard_safety_detection_recall: EvalMetric
    output_guardrail_soft_fact_detection_rate: EvalMetric
    output_guardrail_violation_precision: EvalMetric
    output_guardrail_safe_allow_precision: EvalMetric
    output_guardrail_false_positive_avoidance: EvalMetric
    output_guardrail_category_accuracy: EvalMetric
    output_guardrail_category_detection_recall: EvalMetric
    output_guardrail_semantic_detection_recall: EvalMetric
    output_guardrail_high_risk_detection_rate: EvalMetric
    output_guardrail_repair_success_rate: EvalMetric
    output_guardrail_repair_trigger_rate: EvalMetric
    output_guardrail_repair_success_given_attempt: EvalMetric
    output_guardrail_end_to_end_repair_rate: EvalMetric
    output_guardrail_repair_recheck_block_rate: EvalMetric


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
    execution_version: ExecutionVersionInfo = Field(default_factory=ExecutionVersionInfo)
    report: EvalReport
    summary: dict[str, int]
    cases: list[EvalCaseTrace] = Field(default_factory=list)
