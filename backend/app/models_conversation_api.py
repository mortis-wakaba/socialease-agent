"""API contracts for unified conversation and module control endpoints."""

from datetime import datetime

from pydantic import Field

from app.models import ChatResponse, SafetyResult
from app.models_conversation import (
    Conversation,
    ConversationEvent,
    ConversationEventPage,
    ModuleProposal,
    ModuleRun,
    StrictConversationModel,
    ConversationStatus,
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


class ConversationModuleTerminateRequest(StrictConversationModel):
    """Explicit owner request to terminate module state."""

    user_id: str = Field(min_length=1)


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
    pending_module_proposals: list[ModuleProposal] = Field(default_factory=list)


class ModuleControlResponse(StrictConversationModel):
    """Result of accepting or explicitly terminating module frames."""

    conversation: Conversation
    active_module_stack: list[ModuleRun] = Field(default_factory=list)
    appended_events: list[ConversationEvent] = Field(default_factory=list)
    response: str


class ConversationUpdateRequest(StrictConversationModel):
    """Optimistic owner update for title or archive state."""

    user_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: ConversationStatus | None = None


class ConversationDeleteRequest(StrictConversationModel):
    """Explicit confirmation for destructive conversation deletion."""

    user_id: str = Field(min_length=1)
    confirm_delete: bool


class ConversationExportResponse(StrictConversationModel):
    """Complete decrypted owner export for one conversation."""

    conversation: Conversation
    events: list[ConversationEvent] = Field(default_factory=list)
    module_runs: list[ModuleRun] = Field(default_factory=list)
    module_proposals: list[ModuleProposal] = Field(default_factory=list)
    exported_at: datetime


class ConversationDeleteResponse(StrictConversationModel):
    """Content-free deletion result safe for audit and UI."""

    conversation_id: str
    deleted: bool
    deleted_counts: dict[str, int] = Field(default_factory=dict)


class ConversationExportCollectionResponse(StrictConversationModel):
    """Complete owner export across all conversations."""

    user_id: str = Field(min_length=1)
    conversations: list[ConversationExportResponse] = Field(default_factory=list)
    exported_at: datetime


class LegacyRoleplayImportResponse(StrictConversationModel):
    """Idempotent result for one owner's legacy role-play backfill."""

    user_id: str = Field(min_length=1)
    scanned_count: int = Field(ge=0)
    imported_count: int = Field(ge=0)
    conversations: list[Conversation] = Field(default_factory=list)
