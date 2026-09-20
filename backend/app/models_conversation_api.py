"""API contracts for unified conversation and module control endpoints."""

from datetime import datetime
from typing import Annotated, Literal

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
from app.models_intent_plan import ConversationIntentPlan


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


class ConversationCommandRecoveryRequest(StrictConversationModel):
    """Owner identity for explicitly abandoning one expired command."""

    user_id: str = Field(min_length=1)


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
    intent_plan: ConversationIntentPlan | None = None
    response: str
    safety_result: SafetyResult
    context_diagnostics: ConversationContextDiagnostics
    workflow_response: ChatResponse | None = None
    crisis_persistence_status: Literal[
        "not_applicable",
        "persisted",
        "degraded_retry_required",
        "safety_state_persisted_command_unconfirmed",
    ] = "not_applicable"


class ConversationStreamChunkEvent(StrictConversationModel):
    """One policy-approved sentence buffer delivered over SSE."""

    type: Literal["response_chunk"] = "response_chunk"
    text: str = Field(min_length=1, max_length=1000)


class ConversationStreamCompletedResult(StrictConversationModel):
    """Durable state plus any explicitly non-durable safety reply."""

    conversation: Conversation
    appended_events: list[ConversationEvent] = Field(default_factory=list)
    active_module_stack: list[ModuleRun] = Field(default_factory=list)
    pending_module_proposal: ModuleProposal | None = None
    transient_response: str | None = Field(default=None, min_length=1)
    crisis_persistence_status: Literal[
        "not_applicable",
        "persisted",
        "degraded_retry_required",
        "safety_state_persisted_command_unconfirmed",
    ] = "not_applicable"


class ConversationCommandRecoveryResponse(StrictConversationModel):
    """Owner-scoped command lifecycle used after retries or page refreshes."""

    status: Literal[
        "not_found", "processing", "recoverable", "completed", "abandoned"
    ]
    result: ConversationStreamCompletedResult | None = None
    can_retry_same_request: bool = False
    can_abandon: bool = False
    safety_recovery_status: Literal[
        "classification_pending",
        "not_required",
        "recovery_pending",
        "completed",
    ] | None = None


class ConversationStreamCompletedEvent(StrictConversationModel):
    """Final durable response delivered after all writes complete."""

    type: Literal["response_completed"] = "response_completed"
    result: ConversationStreamCompletedResult


class ConversationStreamErrorEvent(StrictConversationModel):
    """Content-free stream failure safe for an authenticated client."""

    type: Literal["response_error"] = "response_error"
    detail: str = Field(min_length=1, max_length=240)


class ConversationStreamHeartbeatEvent(StrictConversationModel):
    """Content-free keepalive emitted while a turn is still processing."""

    type: Literal["response_heartbeat"] = "response_heartbeat"


ConversationStreamEvent = Annotated[
    ConversationStreamChunkEvent
    | ConversationStreamCompletedEvent
    | ConversationStreamErrorEvent
    | ConversationStreamHeartbeatEvent,
    Field(discriminator="type"),
]


class ConversationDetailResponse(StrictConversationModel):
    """Conversation metadata plus one cursor-paginated timeline page."""

    conversation: Conversation
    events: ConversationEventPage
    active_module_stack: list[ModuleRun] = Field(default_factory=list)
    pending_module_proposals: list[ModuleProposal] = Field(default_factory=list)
    intent_plan: ConversationIntentPlan | None = None


class ModuleControlResponse(StrictConversationModel):
    """Result of accepting or explicitly terminating module frames."""

    conversation: Conversation
    active_module_stack: list[ModuleRun] = Field(default_factory=list)
    appended_events: list[ConversationEvent] = Field(default_factory=list)
    response: str
    intent_plan: ConversationIntentPlan | None = None


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
    intent_plan: ConversationIntentPlan | None = None
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
