"""User-facing models for inspecting and controlling agent memory."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models_long_term_memory import (
    MemoryEvidenceType,
    MemoryPolicyReason,
    MemoryProposalStatus,
    MemoryRecordStatus,
    MemorySourceType,
    MemoryType,
    PracticeThreadCheckpoint,
)
from app.models_memory import (
    AgentMemoryType,
    PracticePreferences,
    UserConsentState,
    UserOnboardingProfile,
)
from app.models_memory_doctor import MemoryDoctorReport
from app.models_roleplay import RoleplayScenario


class StableMemoryView(BaseModel):
    """Low-sensitivity stable settings shown separately from agent memories."""

    model_config = ConfigDict(extra="forbid")

    consent_state: UserConsentState
    practice_preferences: PracticePreferences
    onboarding_profile: UserOnboardingProfile
    disabled_memory_types: list[AgentMemoryType] = Field(
        default_factory=list,
        max_length=5,
    )


class EpisodicMemoryView(BaseModel):
    """Editable user-facing projection without internal hashes or index keys."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    memory_type: MemoryType
    summary: str
    scenario_type: RoleplayScenario | None = None
    source_type: MemorySourceType
    evidence_type: MemoryEvidenceType
    confidence: float
    status: MemoryRecordStatus
    saved_reason: str
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime
    last_retrieved_at: datetime | None = None
    expires_at: datetime | None = None
    version: int


class MemoryProposalView(BaseModel):
    """Pending candidate requiring a direct user decision."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    memory_type: MemoryType
    summary: str
    scenario_type: RoleplayScenario | None = None
    source_type: MemorySourceType
    evidence_type: MemoryEvidenceType
    confidence: float
    status: MemoryProposalStatus
    saved_reason: MemoryPolicyReason
    occurred_at: datetime
    created_at: datetime
    expires_at: datetime
    version: int


class MemoryCenterResponse(BaseModel):
    """Complete bounded Memory Center snapshot for one owner."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    stable_memory: StableMemoryView
    active_threads: list[PracticeThreadCheckpoint] = Field(
        default_factory=list,
        max_length=100,
    )
    memories: list[EpisodicMemoryView] = Field(default_factory=list, max_length=500)
    pending_proposals: list[MemoryProposalView] = Field(
        default_factory=list,
        max_length=100,
    )
    doctor: MemoryDoctorReport
    memory_history_distinction: str = (
        "Agent Memory 是经授权后用于未来个性化的摘要；聊天历史是原会话记录，"
        "不会因为出现在历史中就自动成为 Agent Memory。"
    )


class MemoryProposalListResponse(BaseModel):
    """Bounded pending-proposal list."""

    user_id: str
    proposals: list[MemoryProposalView] = Field(default_factory=list, max_length=100)


class MemoryEditRequest(BaseModel):
    """Optimistic-lock request for editing one safe summary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)


class MemoryVersionRequest(BaseModel):
    """Optimistic-lock request for lifecycle changes."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class MemoryMutationResponse(BaseModel):
    """Result of editing or changing one memory lifecycle."""

    user_id: str
    memory: EpisodicMemoryView | None = None
    deleted: bool = False


class MemoryProposalDecisionResponse(BaseModel):
    """Result of confirming or rejecting one proposal."""

    user_id: str
    proposal_id: str
    status: MemoryProposalStatus
    memory: EpisodicMemoryView | None = None
