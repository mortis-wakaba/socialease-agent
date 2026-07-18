"""Shared Pydantic models and enums for API and workflow state."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models_llm import LLMUsage


class RiskLevel(str, Enum):
    """Risk levels emitted by the safety classifier."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRISIS = "crisis"


class Intent(str, Enum):
    """Supported user intents for the SocialEase workflow."""

    EMOTIONAL_SUPPORT = "emotional_support"
    ROLEPLAY_PRACTICE = "roleplay_practice"
    CBT_WORKSHEET = "cbt_worksheet"
    EXPOSURE_PLANNING = "exposure_planning"
    CAMPUS_RESOURCE_QUERY = "campus_resource_query"
    PROGRESS_REVIEW = "progress_review"
    CLARIFICATION_NEEDED = "clarification_needed"
    OUT_OF_SCOPE = "out_of_scope"
    CRISIS = "crisis"


class SafetyResult(BaseModel):
    """Output from the safety classifier."""

    risk_level: RiskLevel
    reason: str
    llm_usage: LLMUsage = LLMUsage()


class IntentResult(BaseModel):
    """Output from the intent router."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    llm_usage: LLMUsage = LLMUsage()


class ChatRequest(BaseModel):
    """Request body for a chat workflow run."""

    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class TraceFieldPolicy(BaseModel):
    """Privacy policy outcome for one trace field."""

    field: str
    persistence_kind: str
    minimized: bool = False
    summarized: bool = False
    policy: str = "default"
    redacted_types: list[str] = Field(default_factory=list)
    original_length: int = 0
    persisted_length: int = 0


class TracePrivacySummary(BaseModel):
    """Product-safe trace privacy metadata."""

    trace_layer: str = "product_safe"
    raw_input_retained: bool = False
    raw_output_retained: bool = False
    fields: list[TraceFieldPolicy] = Field(default_factory=list)


class TraceRecord(BaseModel):
    """Trace of one agent workflow run."""

    run_id: str
    request_id: str | None = None
    user_id: str
    session_id: str | None = None
    intervention_plan_id: str | None = None
    input: str
    safety_result: SafetyResult
    intent_result: IntentResult
    selected_skill: str | None = None
    selected_agent: str
    action: str | None = None
    permission_action: str | None = None
    permission_reason: str | None = None
    context_selected_fields: list[str] = Field(default_factory=list)
    context_field_sources: dict[str, list[str]] = Field(default_factory=dict)
    context_dropped_fields: list[str] = Field(default_factory=list)
    agent_loop_used: bool = False
    agent_loop_stop_reason: str | None = None
    agent_loop_steps: list[dict[str, Any]] = Field(default_factory=list)
    output_guardrail_action: str | None = None
    output_guardrail_categories: list[str] = Field(default_factory=list)
    output_guardrail_semantic_checked: bool = False
    output_guardrail_semantic_failed: bool = False
    output_guardrail_semantic_error_type: str | None = None
    output_guardrail_semantic_schema_error_code: str | None = None
    output_guardrail_semantic_schema_error_field: str | None = None
    output_guardrail_semantic_retry_attempted: bool = False
    output_guardrail_violation_tier: str | None = None
    output_guardrail_repair_attempted: bool = False
    output_guardrail_repair_succeeded: bool = False
    output_guardrail_recheck_action: str | None = None
    output: str
    product_safe: bool = True
    privacy_summary: TracePrivacySummary = Field(default_factory=TracePrivacySummary)
    latency_ms: float
    errors: list[str] = Field(default_factory=list)
    error_categories: list[str] = Field(default_factory=list)
    created_at: datetime


class ChatResponse(BaseModel):
    """Response returned by the chat endpoint."""

    run_id: str
    risk_level: RiskLevel
    intent: Intent
    response: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    trace: TraceRecord
