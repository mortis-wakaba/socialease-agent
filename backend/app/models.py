"""Shared Pydantic models and enums for API and workflow state."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    CRISIS = "crisis"


class SafetyResult(BaseModel):
    """Output from the safety classifier."""

    risk_level: RiskLevel
    reason: str


class IntentResult(BaseModel):
    """Output from the intent router."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class ChatRequest(BaseModel):
    """Request body for a chat workflow run."""

    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class TraceRecord(BaseModel):
    """Trace of one agent workflow run."""

    run_id: str
    user_id: str
    input: str
    safety_result: SafetyResult
    intent_result: IntentResult
    selected_agent: str
    output: str
    latency_ms: float
    errors: list[str] = Field(default_factory=list)
    created_at: datetime


class ChatResponse(BaseModel):
    """Response returned by the chat endpoint."""

    run_id: str
    risk_level: RiskLevel
    intent: Intent
    response: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    trace: TraceRecord

