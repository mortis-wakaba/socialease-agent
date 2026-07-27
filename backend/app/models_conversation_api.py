"""API contracts for unified conversation and module control endpoints."""

from pydantic import Field

from app.models import ChatResponse, SafetyResult
from app.models_conversation import (
    Conversation,
    ConversationEvent,
    ConversationEventPage,
    ModuleProposal,
    ModuleRun,
    StrictConversationModel,
)
from app.models_conversation_context import ConversationContextDiagnostics


class ConversationCreateRequest(StrictConversationModel):
    """Create a conversation after an explicit persistence notice."""

    user_id: str = Field(min_length=1)
    title: str = Field(default="新对话", min_length=1, max_length=160)
    history_notice_version: str = Field(min_length=1)
    history_notice_acknowledged: bool


class ConversationMessageRequest(StrictConversationModel):
    """Append one idempotent user message to a conversation."""

    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=8, max_length=200)


class ConversationModuleDecisionRequest(StrictConversationModel):
    """Accept or reject a proposal with its application-bound request hash."""

    user_id: str = Field(min_length=1)
    request_hash: str = Field(min_length=32, max_length=128)


class ConversationMessageResponse(StrictConversationModel):
    """Unified response for general and module-aware conversation turns."""

    conversation: Conversation
    appended_events: list[ConversationEvent] = Field(default_factory=list)
    active_module_stack: list[ModuleRun] = Field(default_factory=list)
    pending_module_proposal: ModuleProposal | None = None
    response: str
    safety_result: SafetyResult
    context_diagnostics: ConversationContextDiagnostics
    workflow_response: ChatResponse | None = None


class ConversationDetailResponse(StrictConversationModel):
    """Conversation metadata plus one cursor-paginated timeline page."""

    conversation: Conversation
    events: ConversationEventPage
    active_module_stack: list[ModuleRun] = Field(default_factory=list)
