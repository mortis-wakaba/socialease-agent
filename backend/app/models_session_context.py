"""Typed models for short-lived role-play context and compaction."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models_roleplay import RoleplayMessageRole


class SessionContextMessage(BaseModel):
    """One raw, TTL-bound message kept outside long-term persistence."""

    model_config = ConfigDict(extra="forbid")

    role: RoleplayMessageRole
    content: str = Field(min_length=1, max_length=8000)
    created_at: datetime


class RoleplayCompactState(BaseModel):
    """Privacy-minimized state distilled from messages outside the recent window."""

    model_config = ConfigDict(extra="forbid")

    user_goal: str | None = Field(default=None, max_length=240)
    current_topic: str | None = Field(default=None, max_length=240)
    expressed_needs: list[str] = Field(default_factory=list, max_length=5)
    attempted_phrases: list[str] = Field(default_factory=list, max_length=5)
    counterpart_position: str | None = Field(default=None, max_length=240)
    unresolved_question: str | None = Field(default=None, max_length=240)
    practiced_skills: list[str] = Field(default_factory=list, max_length=6)
    compacted_through_message: int = Field(default=0, ge=0)
    source_message_count: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    updated_at: datetime


class RoleplaySessionContext(BaseModel):
    """Redis payload for one active, user-owned role-play session."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    messages: list[SessionContextMessage] = Field(default_factory=list, max_length=64)
    compact_state: RoleplayCompactState | None = None
    version: int = Field(default=1, ge=1)
    updated_at: datetime


class RoleplayContextDiagnostics(BaseModel):
    """Value-safe diagnostics for one role-play prompt context build."""

    model_config = ConfigDict(extra="forbid")

    backend: str
    available: bool
    fallback_used: bool = False
    recent_message_count: int = Field(default=0, ge=0)
    compact_state_used: bool = False
    compaction_triggered: bool = False
    compacted_message_count: int = Field(default=0, ge=0)
    estimated_input_tokens: int = Field(default=0, ge=0)
    input_token_budget: int = Field(default=0, ge=0)
    budget_utilization: float = Field(default=0.0, ge=0.0)
    token_estimator_backend: str = "unknown"
    token_estimator_model: str | None = None
    error_category: str | None = None


class RoleplayPromptContext(BaseModel):
    """Bounded session context passed to one role-play generation call."""

    model_config = ConfigDict(extra="forbid")

    recent_messages: list[str] = Field(default_factory=list, max_length=20)
    compact_state: RoleplayCompactState | None = None
    diagnostics: RoleplayContextDiagnostics


class CompactGenerationPayload(BaseModel):
    """Strict model-facing compact payload before application metadata is added."""

    model_config = ConfigDict(extra="forbid")

    user_goal: str | None = Field(default=None, max_length=240)
    current_topic: str | None = Field(default=None, max_length=240)
    expressed_needs: list[str] = Field(default_factory=list, max_length=5)
    attempted_phrases: list[str] = Field(default_factory=list, max_length=5)
    counterpart_position: str | None = Field(default=None, max_length=240)
    unresolved_question: str | None = Field(default=None, max_length=240)
    practiced_skills: list[str] = Field(default_factory=list, max_length=6)


def context_diagnostics_payload(
    diagnostics: RoleplayContextDiagnostics,
) -> dict[str, Any]:
    """Return JSON-compatible, value-safe context diagnostics."""
    return diagnostics.model_dump(mode="json")
