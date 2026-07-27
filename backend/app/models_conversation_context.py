"""Bounded context and durable summary contracts for conversations."""

from datetime import datetime
from hashlib import sha256

from pydantic import Field

from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ModuleRun,
    StrictConversationModel,
)


class ConversationCompactPayload(StrictConversationModel):
    """Validated semantic fields generated during timeline compaction."""

    user_stated_goals: list[str] = Field(default_factory=list, max_length=5)
    current_topics: list[str] = Field(default_factory=list, max_length=5)
    open_questions: list[str] = Field(default_factory=list, max_length=5)
    module_outcomes: list[str] = Field(default_factory=list, max_length=5)


class ConversationCompactSummary(ConversationCompactPayload):
    """Durable, privacy-checked summary projection over older events."""

    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    compacted_through_sequence: int = Field(default=0, ge=0)
    source_event_count: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    updated_at: datetime


class ConversationContextBudgets(StrictConversationModel):
    """Independent sub-budgets constrained by one total model-context budget."""

    total_tokens: int = Field(default=6000, ge=512, le=32_000)
    current_request_tokens: int = Field(default=1200, ge=128, le=8000)
    recent_events_tokens: int = Field(default=2400, ge=128, le=12_000)
    summary_tokens: int = Field(default=1000, ge=128, le=4000)
    module_stack_tokens: int = Field(default=600, ge=64, le=3000)
    active_memory_tokens: int = Field(default=800, ge=0, le=4000)


class ConversationContextDiagnostics(StrictConversationModel):
    """Content-free diagnostics safe for product traces."""

    conversation_id_hash: str = Field(pattern=r"^[0-9a-f]{16}$")
    recent_event_count: int = Field(ge=0)
    recent_event_sequence_start: int | None = Field(default=None, ge=1)
    recent_event_sequence_end: int | None = Field(default=None, ge=1)
    compact_summary_version: int | None = Field(default=None, ge=1)
    active_module_count: int = Field(ge=0)
    selected_memory_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    total_token_budget: int = Field(ge=1)
    dropped_sections: list[str] = Field(default_factory=list)
    tokenizer_backend: str = Field(min_length=1, max_length=64)


class ConversationWorkingContext(StrictConversationModel):
    """One bounded prompt projection assembled for the current request."""

    conversation_id: str = Field(min_length=1)
    current_user_message: str = Field(min_length=1, max_length=20_000)
    recent_events: list[ConversationEvent] = Field(default_factory=list)
    compact_summary: ConversationCompactSummary | None = None
    active_module_stack: list[ModuleRun] = Field(default_factory=list)
    selected_agent_memory: list[str] = Field(default_factory=list, max_length=8)
    diagnostics: ConversationContextDiagnostics


class ConversationPromptEvent(StrictConversationModel):
    """Minimal historical event allowed to enter a generation prompt."""

    event_type: ConversationEventType
    role: ConversationEventRole
    content: str = Field(min_length=1, max_length=20_000)


class ConversationPromptContext(StrictConversationModel):
    """Trusted bounded conversation continuity passed into the harness."""

    recent_events: list[ConversationPromptEvent] = Field(
        default_factory=list,
        max_length=32,
    )
    compact_summary: ConversationCompactPayload | None = None


def conversation_id_hash(conversation_id: str) -> str:
    """Return a stable content-free identifier for diagnostics."""
    return sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
