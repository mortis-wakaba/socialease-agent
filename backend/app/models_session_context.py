"""Typed role-play overlay and durable checkpoint prompt projections."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models_conversation_context import (
    ConversationCompactPayload,
    ConversationContextDiagnostics,
)
from app.models_module_overlay import ParentResumeProjection


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


class RoleplayPromptContext(BaseModel):
    """Shared conversation projection passed to one role-play generation call."""

    model_config = ConfigDict(extra="forbid")

    recent_messages: list[str] = Field(default_factory=list, max_length=20)
    compact_state: RoleplayCompactState | None = None
    shared_summary: ConversationCompactPayload | None = None
    parent_resume_projections: list[ParentResumeProjection] = Field(
        default_factory=list,
        max_length=2,
    )
    retrieved_memories: list[str] = Field(default_factory=list, max_length=3)
    diagnostics: ConversationContextDiagnostics


class DurableCheckpointContext(BaseModel):
    """Token-bounded active memory reconstructed from one exact thread."""

    model_config = ConfigDict(extra="forbid")

    compact_state: RoleplayCompactState
    checkpoint_version: int = Field(ge=1)
    estimated_tokens: int = Field(ge=0)
    token_budget: int = Field(ge=1)
