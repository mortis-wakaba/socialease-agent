"""Typed packets and value-free diagnostics for active memory assembly."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models_context import SkillContextProjection
from app.models_session_context import DurableCheckpointContext


class ActiveMemoryLayer(str, Enum):
    """Ordered memory layers exposed to a skill."""

    STABLE = "stable"
    WORKING = "working"
    EPISODIC = "episodic"


class ActiveMemoryDropReason(str, Enum):
    """Value-free reasons why a candidate did not enter active context."""

    NOT_ALLOWED_FOR_SKILL = "not_allowed_for_skill"
    CONSENT_REQUIRED = "consent_required"
    SCOPE_MISMATCH = "scope_mismatch"
    CURRENT_REQUEST_CONFLICT = "current_request_conflict"
    TOKEN_BUDGET = "token_budget"
    MAX_ITEMS = "max_items"


class ActiveMemorySelectionRecord(BaseModel):
    """Trace-safe decision metadata that never contains a memory body."""

    model_config = ConfigDict(extra="forbid")

    memory_id_hash: str = Field(pattern=r"^[0-9a-f]{16}$")
    memory_layer: ActiveMemoryLayer
    memory_type: str = Field(min_length=1, max_length=64)
    source_type: str = Field(min_length=1, max_length=64)
    confidence: str | None = Field(default=None, max_length=32)
    retrieval_method: str | None = Field(default=None, max_length=32)
    retrieval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    selected: bool
    drop_reason: ActiveMemoryDropReason | None = None
    estimated_tokens: int = Field(default=0, ge=0)


class ActiveMemoryPacket(BaseModel):
    """Deterministically assembled model context plus content-free decisions."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1, max_length=128)
    stable_memory: SkillContextProjection
    working_memory: DurableCheckpointContext | None = None
    episodic_memories: list[str] = Field(default_factory=list, max_length=3)
    selections: list[ActiveMemorySelectionRecord] = Field(
        default_factory=list,
        max_length=64,
    )
    estimated_tokens: int = Field(ge=0)
    token_budget: int = Field(ge=1)
    assembled_at: datetime

    def trace_metadata(self) -> dict[str, object]:
        """Return diagnostics safe for persistence in product traces."""
        return {
            "skill_name": self.skill_name,
            "estimated_tokens": self.estimated_tokens,
            "token_budget": self.token_budget,
            "selected_counts": {
                layer.value: sum(
                    1
                    for item in self.selections
                    if item.memory_layer == layer and item.selected
                )
                for layer in ActiveMemoryLayer
            },
            "selections": [
                item.model_dump(mode="json") for item in self.selections
            ],
        }
