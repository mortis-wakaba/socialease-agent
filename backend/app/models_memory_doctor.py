"""Content-free diagnostics for user-scoped memory quality checks."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryDoctorIssueCode(str, Enum):
    """Stable diagnostic codes independent from user memory text."""

    DUPLICATE_MEMORY = "duplicate_memory"
    CONFLICTING_MEMORY = "conflicting_memory"
    STALE_UNUSED_MEMORY = "stale_unused_memory"
    CONSENT_INACTIVE_MEMORY = "consent_inactive_memory"
    TYPE_PERSONALIZATION_DISABLED = "type_personalization_disabled"
    SOURCE_REFERENCE_MISSING = "source_reference_missing"
    TIMESTAMP_INVALID = "timestamp_invalid"
    ORPHAN_EMBEDDING = "orphan_embedding"
    ACTIVE_MEMORY_OVER_BUDGET = "active_memory_over_budget"
    STALE_CHECKPOINT = "stale_checkpoint"
    PENDING_PROPOSAL_AGED = "pending_proposal_aged"


class MemoryDoctorSeverity(str, Enum):
    """User-facing diagnostic priority."""

    INFO = "info"
    WARNING = "warning"
    ACTION_REQUIRED = "action_required"


class MemoryDoctorCheckStatus(str, Enum):
    """Outcome of one doctor rule."""

    PASSED = "passed"
    ISSUES_FOUND = "issues_found"
    NOT_APPLICABLE = "not_applicable"


class MemoryDoctorSubjectType(str, Enum):
    """Value-free subject categories used in reports."""

    EPISODIC_MEMORY = "episodic_memory"
    THREAD_CHECKPOINT = "thread_checkpoint"
    MEMORY_PROPOSAL = "memory_proposal"
    EMBEDDING_INDEX = "embedding_index"
    ACTIVE_MEMORY_PACKET = "active_memory_packet"
    USER_MEMORY_SETTINGS = "user_memory_settings"


class MemoryDoctorIssue(BaseModel):
    """One content-free finding with hashed subject identifiers."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    code: MemoryDoctorIssueCode
    severity: MemoryDoctorSeverity
    subject_type: MemoryDoctorSubjectType
    subject_id_hashes: list[
        Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    ] = Field(default_factory=list, max_length=10)
    affected_count: int = Field(ge=1)
    metadata: dict[str, int | float | str | bool] = Field(default_factory=dict)
    recommendation_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class MemoryDoctorCheck(BaseModel):
    """Summary for a rule, including intentionally unavailable checks."""

    model_config = ConfigDict(extra="forbid")

    code: MemoryDoctorIssueCode
    status: MemoryDoctorCheckStatus
    issue_count: int = Field(ge=0)
    detail_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class MemoryDoctorThresholds(BaseModel):
    """Versioned deterministic thresholds reported with each run."""

    stale_memory_days: int = Field(ge=1)
    stale_checkpoint_days: int = Field(ge=1)
    pending_proposal_days: int = Field(ge=1)
    active_memory_token_budget: int = Field(ge=1)
    conflict_term_overlap: int = Field(ge=1)


class MemoryDoctorScannedCounts(BaseModel):
    """Bounded counts that reveal no memory content."""

    model_config = ConfigDict(extra="forbid")

    episodic_memories: int = Field(ge=0, le=500)
    thread_checkpoints: int = Field(ge=0, le=500)
    pending_proposals: int = Field(ge=0, le=500)


class MemoryDoctorReport(BaseModel):
    """Read-only report that never includes a memory or proposal body."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    policy_version: Literal["memory-doctor-v1"] = "memory-doctor-v1"
    generated_at: datetime
    scanned_counts: MemoryDoctorScannedCounts
    thresholds: MemoryDoctorThresholds
    checks: list[MemoryDoctorCheck]
    issues: list[MemoryDoctorIssue] = Field(default_factory=list, max_length=500)
    issues_truncated: bool = False
    auto_fix_applied: Literal[False] = False
    contains_memory_content: Literal[False] = False
