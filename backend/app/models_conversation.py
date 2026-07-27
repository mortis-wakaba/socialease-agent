"""Strict domain contracts for unified conversations and nested modules."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator



HISTORY_NOTICE_VERSION = "2026-07-01"
MAX_MODULE_DEPTH = 3


class StrictConversationModel(BaseModel):
    """Base model that rejects undeclared conversation fields."""

    model_config = ConfigDict(extra="forbid")


class ConversationStatus(str, Enum):
    """Lifecycle state for a user-owned conversation."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ConversationEventType(str, Enum):
    """Supported append-only timeline event types."""

    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    MODULE_PROPOSED = "module_proposed"
    MODULE_STARTED = "module_started"
    MODULE_MESSAGE = "module_message"
    MODULE_SUSPENDED = "module_suspended"
    MODULE_RESUMED = "module_resumed"
    MODULE_COMPLETED = "module_completed"
    MODULE_TERMINATED = "module_terminated"
    CRISIS_INPUT = "crisis_input"
    CRISIS_ESCALATED = "crisis_escalated"


class ConversationEventRole(str, Enum):
    """Origin of a user-visible timeline event."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ModuleType(str, Enum):
    """Application-approved modules available inside a conversation."""

    ROLEPLAY = "roleplay"
    WORKSHEET = "worksheet"
    EXPOSURE = "exposure"
    RESOURCE = "resource"


class ModuleProposalStatus(str, Enum):
    """Lifecycle state of a model-suggested module proposal."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ModuleRunStatus(str, Enum):
    """Lifecycle state of one module frame."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class ModuleProposalReason(str, Enum):
    """Bounded reasons that may justify offering a module."""

    EXPLICIT_PRACTICE_REQUEST = "explicit_practice_request"
    STRUCTURED_REFLECTION_MAY_HELP = "structured_reflection_may_help"
    GRADED_PRACTICE_MAY_HELP = "graded_practice_may_help"
    RESOURCE_LOOKUP_REQUESTED = "resource_lookup_requested"


class RoleplayParameters(StrictConversationModel):
    """Validated inputs offered for a role-play module."""

    kind: Literal["roleplay"] = "roleplay"
    scenario_description: str = Field(min_length=1, max_length=1200)
    practice_goal: str | None = Field(default=None, min_length=1, max_length=400)
    difficulty: int = Field(default=2, ge=1, le=5)


class WorksheetParameters(StrictConversationModel):
    """Validated inputs offered for a worksheet module."""

    kind: Literal["worksheet"] = "worksheet"
    situation: str = Field(min_length=1, max_length=1200)


class ExposureParameters(StrictConversationModel):
    """Validated inputs offered for a graded exposure module."""

    kind: Literal["exposure"] = "exposure"
    goal: str = Field(min_length=1, max_length=500)
    starting_anxiety: int | None = Field(default=None, ge=0, le=10)


class ResourceParameters(StrictConversationModel):
    """Validated inputs offered for a grounded resource lookup."""

    kind: Literal["resource"] = "resource"
    query: str = Field(min_length=1, max_length=500)


ModuleParameters = Annotated[
    RoleplayParameters
    | WorksheetParameters
    | ExposureParameters
    | ResourceParameters,
    Field(discriminator="kind"),
]


class ModuleProposalEventPayload(StrictConversationModel):
    """User-visible metadata for a proposed module."""

    kind: Literal["module_proposal"] = "module_proposal"
    proposal_id: str = Field(min_length=1)
    proposed_module: ModuleType
    reason_code: ModuleProposalReason


class ModuleLifecycleEventPayload(StrictConversationModel):
    """User-visible metadata for a module lifecycle transition."""

    kind: Literal["module_lifecycle"] = "module_lifecycle"
    module_run_id: str = Field(min_length=1)
    module_type: ModuleType
    parent_module_run_id: str | None = None


class CrisisEscalatedEventPayload(StrictConversationModel):
    """Minimal metadata for a safety-preemption event."""

    kind: Literal["crisis_escalated"] = "crisis_escalated"
    risk_level: Literal["crisis"] = "crisis"


class RoleplayMessageEventPayload(StrictConversationModel):
    """Structured projection of one role-play module turn."""

    kind: Literal["roleplay_message"] = "roleplay_message"
    session_id: str = Field(min_length=1)
    blocked: bool = False


class WorksheetMessageEventPayload(StrictConversationModel):
    """Structured projection of one worksheet module turn."""

    kind: Literal["worksheet_message"] = "worksheet_message"
    worksheet_id: str = Field(min_length=1)
    completed: bool = False
    missing_fields: list[str] = Field(default_factory=list, max_length=16)


class ExposureMessageEventPayload(StrictConversationModel):
    """Structured projection of one exposure module turn."""

    kind: Literal["exposure_message"] = "exposure_message"
    plan_id: str | None = None
    awaiting_anxiety_level: bool = False
    blocked: bool = False


class ResourceMessageEventPayload(StrictConversationModel):
    """Structured projection of one grounded resource module turn."""

    kind: Literal["resource_message"] = "resource_message"
    search_session_id: str | None = None
    citation_count: int = Field(default=0, ge=0, le=20)
    citation_ids: list[str] = Field(default_factory=list, max_length=10)
    unknown: bool = False


ConversationEventPayload = Annotated[
    ModuleProposalEventPayload
    | ModuleLifecycleEventPayload
    | CrisisEscalatedEventPayload
    | RoleplayMessageEventPayload
    | WorksheetMessageEventPayload
    | ExposureMessageEventPayload
    | ResourceMessageEventPayload,
    Field(discriminator="kind"),
]


class Conversation(StrictConversationModel):
    """Top-level owner scope for a continuous user-visible timeline."""

    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=160)
    status: ConversationStatus = ConversationStatus.ACTIVE
    active_module_depth: int = Field(default=0, ge=0, le=MAX_MODULE_DEPTH)
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
    history_notice_version: str = Field(min_length=1)


class ConversationEvent(StrictConversationModel):
    """One ordered, append-only event in a conversation."""

    event_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    event_type: ConversationEventType
    role: ConversationEventRole
    content: str = Field(default="", max_length=20_000)
    structured_payload: ConversationEventPayload | None = None
    module_run_id: str | None = None
    parent_module_run_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    created_at: datetime

    @model_validator(mode="after")
    def validate_payload_for_event_type(self) -> "ConversationEvent":
        """Keep persisted structured payloads bounded by their event type."""
        expected_payloads = {
            ConversationEventType.MODULE_PROPOSED: ModuleProposalEventPayload,
            ConversationEventType.MODULE_STARTED: ModuleLifecycleEventPayload,
            ConversationEventType.MODULE_SUSPENDED: ModuleLifecycleEventPayload,
            ConversationEventType.MODULE_RESUMED: ModuleLifecycleEventPayload,
            ConversationEventType.MODULE_COMPLETED: ModuleLifecycleEventPayload,
            ConversationEventType.MODULE_TERMINATED: ModuleLifecycleEventPayload,
            ConversationEventType.CRISIS_ESCALATED: CrisisEscalatedEventPayload,
            ConversationEventType.MODULE_MESSAGE: (
                RoleplayMessageEventPayload,
                WorksheetMessageEventPayload,
                ExposureMessageEventPayload,
                ResourceMessageEventPayload,
            ),
        }
        expected = expected_payloads.get(self.event_type)
        if (
            self.event_type == ConversationEventType.MODULE_MESSAGE
            and self.role == ConversationEventRole.USER
            and self.structured_payload is None
        ):
            return self
        if expected is None and self.structured_payload is not None:
            raise ValueError("this event type does not accept a structured payload")
        if expected is not None and not isinstance(self.structured_payload, expected):
            raise ValueError("structured payload does not match event type")
        return self


class ModuleProposal(StrictConversationModel):
    """Validated, expiring option that requires an explicit user decision."""

    proposal_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    proposed_module: ModuleType
    source_event_id: str | None = Field(default=None, min_length=1)
    reason_code: ModuleProposalReason
    bounded_parameters: ModuleParameters
    status: ModuleProposalStatus = ModuleProposalStatus.PENDING
    request_hash: str = Field(min_length=32, max_length=128)
    expires_at: datetime
    created_at: datetime

    @model_validator(mode="after")
    def parameters_match_module(self) -> "ModuleProposal":
        """Reject parameters for a different module capability."""
        if self.bounded_parameters.kind != self.proposed_module.value:
            raise ValueError("bounded parameters do not match proposed module")
        return self


class ModuleRun(StrictConversationModel):
    """One stateful or read-only module frame in the conversation stack."""

    module_run_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    module_type: ModuleType
    source_event_id: str | None = Field(default=None, min_length=1)
    parent_module_run_id: str | None = None
    depth: int = Field(ge=1, le=MAX_MODULE_DEPTH)
    status: ModuleRunStatus = ModuleRunStatus.ACTIVE
    module_parameters: ModuleParameters
    domain_session_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_terminal_timestamp(self) -> "ModuleRun":
        """Require an end timestamp only for terminal module runs."""
        terminal = self.status in {
            ModuleRunStatus.COMPLETED,
            ModuleRunStatus.TERMINATED,
        }
        if terminal != (self.ended_at is not None):
            raise ValueError("ended_at must be set exactly for terminal module runs")
        return self


class ConversationPage(StrictConversationModel):
    """Cursor-paginated conversations ordered from newest to oldest."""

    items: list[Conversation] = Field(default_factory=list)
    next_cursor: str | None = None


class ConversationEventPage(StrictConversationModel):
    """Cursor-paginated events ordered by ascending sequence number."""

    items: list[ConversationEvent] = Field(default_factory=list)
    next_cursor: str | None = None


class ConversationImportSnapshot(StrictConversationModel):
    """Complete immutable timeline used for idempotent legacy backfills."""

    source_type: Literal["roleplay"]
    source_id: str = Field(min_length=1)
    conversation: Conversation
    events: list[ConversationEvent] = Field(min_length=1)
    module_runs: list[ModuleRun] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_snapshot_scope(self) -> "ConversationImportSnapshot":
        """Require one archived owner scope and a contiguous event timeline."""
        conversation = self.conversation
        if conversation.status != ConversationStatus.ARCHIVED:
            raise ValueError("imported conversations must be read-only")
        if conversation.active_module_depth != 0:
            raise ValueError("imported conversations cannot have active modules")
        expected_sequences = list(range(1, len(self.events) + 1))
        if [event.sequence_no for event in self.events] != expected_sequences:
            raise ValueError("imported event sequence must be contiguous")
        for item in [*self.events, *self.module_runs]:
            if (
                item.conversation_id != conversation.conversation_id
                or item.user_id != conversation.user_id
            ):
                raise ValueError("imported records must share owner scope")
        if any(
            run.status not in {
                ModuleRunStatus.COMPLETED,
                ModuleRunStatus.TERMINATED,
            }
            for run in self.module_runs
        ):
            raise ValueError("imported module runs must be terminal")
        return self
