"""Pydantic models for role-play practice sessions."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models import SafetyResult
from app.models_llm import LLMUsage
from app.models_knowledge import Citation
from app.models_scenario import ScenarioSpec


class RoleplayMessageRole(str, Enum):
    """Message roles within a role-play session."""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class RoleplaySessionStatus(str, Enum):
    """Lifecycle status for a role-play practice session."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class RoleplayMessageFeatures(BaseModel):
    """Privacy-safe derived features for one role-play user message."""

    char_count: int = Field(default=0, ge=0)
    sentence_count: int = Field(default=0, ge=0)
    question_count: int = Field(default=0, ge=0)
    first_person_count: int = Field(default=0, ge=0)
    reason_marker_count: int = Field(default=0, ge=0)
    request_marker_count: int = Field(default=0, ge=0)
    boundary_marker_count: int = Field(default=0, ge=0)
    empathy_marker_count: int = Field(default=0, ge=0)
    politeness_marker_count: int = Field(default=0, ge=0)
    specificity_marker_count: int = Field(default=0, ge=0)
    collaborative_marker_count: int = Field(default=0, ge=0)
    repair_marker_count: int = Field(default=0, ge=0)
    has_reason: bool = False
    has_request: bool = False
    has_boundary_statement: bool = False
    has_empathy_marker: bool = False
    has_specific_time_or_place: bool = False
    has_polite_opening: bool = False
    has_collaborative_offer: bool = False
    has_repair_or_acknowledgement: bool = False
    sensitive_detected: list[str] = Field(default_factory=list)


class RoleplayMessage(BaseModel):
    """One message in a role-play session."""

    role: RoleplayMessageRole
    content: str
    created_at: datetime
    features: RoleplayMessageFeatures | None = None


class RoleplayGuidance(BaseModel):
    """Retrieved social-skills guidance used to ground a role-play session."""

    query: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)
    no_guidance_found: bool = False


class RoleplaySession(BaseModel):
    """Persisted role-play session state."""

    session_id: str
    user_id: str = Field(min_length=1)
    scenario: str | None = Field(default=None, max_length=128)
    scenario_spec: ScenarioSpec | None = None
    difficulty: int = Field(ge=1, le=5)
    status: RoleplaySessionStatus = RoleplaySessionStatus.ACTIVE
    messages: list[RoleplayMessage] = Field(default_factory=list)
    retrieved_guidance: RoleplayGuidance
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_scenario_contract(self) -> "RoleplaySession":
        """Allow old rows to load while requiring one scenario representation."""
        if self.scenario is None and self.scenario_spec is None:
            raise ValueError("role-play session requires scenario_spec")
        return self


class RoleplayStartRequest(BaseModel):
    """Request body for starting a role-play session."""

    user_id: str = Field(min_length=1)
    scenario_description: str = Field(min_length=1, max_length=1200)
    practice_goal: str | None = Field(default=None, min_length=1, max_length=400)
    difficulty: int = Field(default=2, ge=1, le=5)


class RoleplayStartResponse(BaseModel):
    """Response returned after creating a role-play session."""

    session: RoleplaySession
    opening_message: str


class RoleplaySessionListResponse(BaseModel):
    """Response returned when listing recent role-play sessions."""

    user_id: str
    sessions: list[RoleplaySession] = Field(default_factory=list)


class RoleplayMessageRequest(BaseModel):
    """Request body for sending a role-play message."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class RoleplayMessageResponse(BaseModel):
    """Response returned after one role-play turn."""

    session: RoleplaySession
    response: str
    safety_result: SafetyResult
    blocked: bool = False
    llm_usage: LLMUsage = LLMUsage()
    context_diagnostics: dict[str, Any] = Field(default_factory=dict)


class RoleplayPauseRequest(BaseModel):
    """Request body for pausing a role-play session."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class RoleplayPauseResponse(BaseModel):
    """Response returned after pausing a role-play session."""

    session: RoleplaySession
    message: str


class RoleplayResumeRequest(BaseModel):
    """Request body for resuming a paused role-play session."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class RoleplayResumeResponse(BaseModel):
    """Response returned after resuming a role-play session."""

    session: RoleplaySession
    message: str


class RoleplayFeedbackRequest(BaseModel):
    """Request body for role-play feedback."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class RoleplayFeedback(BaseModel):
    """Structured feedback for a role-play session."""

    clarity_score: int = Field(ge=1, le=5)
    naturalness_score: int = Field(ge=1, le=5)
    assertiveness_score: int = Field(ge=1, le=5)
    empathy_score: int = Field(ge=1, le=5)
    rubric_breakdown: list["RoleplayRubricBreakdown"] = Field(default_factory=list)
    strengths: list[str]
    suggestions: list[str]
    next_try_prompt: str
    citations: list[Citation] = Field(default_factory=list)


class RoleplayRubricSignal(BaseModel):
    """One privacy-safe signal used by the role-play feedback rubric."""

    name: str
    label: str
    present: bool
    weight: int = Field(ge=0, le=2)


class RoleplayRubricBreakdown(BaseModel):
    """Explanation for one non-clinical role-play feedback dimension."""

    dimension: str
    score: int = Field(ge=1, le=5)
    signals: list[RoleplayRubricSignal] = Field(default_factory=list)
    rationale: str


class RoleplayFeedbackResponse(BaseModel):
    """Response returned by the role-play feedback endpoint."""

    session: RoleplaySession
    feedback: RoleplayFeedback
