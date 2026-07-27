"""Typed module state projected on top of one shared conversation window."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.models_conversation import ModuleType, StrictConversationModel


class RoleplayOverlay(StrictConversationModel):
    """Role-play state that is not a duplicate conversation transcript."""

    kind: Literal["roleplay"] = "roleplay"
    scenario_summary: str = Field(min_length=1, max_length=500)
    practice_goal: str | None = Field(default=None, max_length=240)
    difficulty: int = Field(ge=1, le=5)
    current_role: str | None = Field(default=None, max_length=120)
    counterpart_position: str | None = Field(default=None, max_length=240)
    attempted_phrases: list[str] = Field(default_factory=list, max_length=5)
    unresolved_question: str | None = Field(default=None, max_length=240)
    resume_point: str | None = Field(default=None, max_length=240)


class WorksheetOverlay(StrictConversationModel):
    """Worksheet progress derived from one durable worksheet record."""

    kind: Literal["worksheet"] = "worksheet"
    worksheet_id: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(default="cbt-v1", max_length=32)
    current_section: str | None = Field(default=None, max_length=64)
    completed_fields: list[str] = Field(default_factory=list, max_length=16)
    missing_fields: list[str] = Field(default_factory=list, max_length=16)
    validation_issue_codes: list[str] = Field(default_factory=list, max_length=16)
    last_confirmed_field: str | None = Field(default=None, max_length=64)


class ExposureOverlay(StrictConversationModel):
    """User-controlled graded-practice state without medical interpretation."""

    kind: Literal["exposure"] = "exposure"
    plan_id: str | None = Field(default=None, max_length=128)
    current_step_id: str | None = Field(default=None, max_length=128)
    current_step_index: int | None = Field(default=None, ge=0, le=100)
    current_step_summary: str | None = Field(default=None, max_length=500)
    current_intensity: int | None = Field(default=None, ge=0, le=10)
    minimum_intensity: int = Field(default=0, ge=0, le=10)
    maximum_intensity: int = Field(default=10, ge=0, le=10)
    attempt_status: Literal[
        "awaiting_rating",
        "ready",
        "in_progress",
        "paused",
        "completed",
    ] = "awaiting_rating"
    last_user_rating: int | None = Field(default=None, ge=0, le=10)
    permission_to_increase: bool = False
    pause_requested: bool = False
    completed_step_ids: list[str] = Field(default_factory=list, max_length=20)
    next_decision: Literal[
        "collect_rating",
        "start",
        "repeat",
        "reduce",
        "hold",
        "complete",
    ] = "collect_rating"

    @model_validator(mode="after")
    def validate_intensity_range(self) -> "ExposureOverlay":
        """Reject an inverted application-owned intensity boundary."""
        if self.minimum_intensity > self.maximum_intensity:
            raise ValueError("minimum intensity cannot exceed maximum intensity")
        return self


class ResourceOverlay(StrictConversationModel):
    """Grounded resource-search references scoped to reviewed knowledge."""

    kind: Literal["resource"] = "resource"
    search_session_id: str = Field(min_length=1, max_length=128)
    query_scope: str | None = Field(default=None, max_length=120)
    knowledge_base_version: str | None = Field(default=None, max_length=64)
    ordered_citation_ids: list[str] = Field(default_factory=list, max_length=10)
    selected_citation_index: int | None = Field(default=None, ge=0, le=9)
    retrieval_unknown: bool = False
    awaiting_user_choice: bool = False


ModuleOverlayPayload = Annotated[
    RoleplayOverlay | WorksheetOverlay | ExposureOverlay | ResourceOverlay,
    Field(discriminator="kind"),
]


class ModuleOverlay(StrictConversationModel):
    """Versioned owner-scoped overlay cached for one module run."""

    conversation_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    module_run_id: str = Field(min_length=1, max_length=64)
    module_type: ModuleType
    parent_module_run_id: str | None = Field(default=None, max_length=64)
    phase: str = Field(min_length=1, max_length=64)
    payload: ModuleOverlayPayload
    version: int = Field(default=1, ge=1)
    updated_at: datetime

    @model_validator(mode="after")
    def validate_payload_type(self) -> "ModuleOverlay":
        """Keep the envelope module type aligned with its typed payload."""
        if self.module_type.value != self.payload.kind:
            raise ValueError("module overlay payload does not match module type")
        return self


class ParentResumeProjection(StrictConversationModel):
    """Minimal suspended-parent state allowed into a child module prompt."""

    module_type: ModuleType
    module_run_id: str = Field(min_length=1, max_length=64)
    resume_point: str | None = Field(default=None, max_length=240)
    version: int = Field(ge=1)
