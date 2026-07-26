"""Typed records for durable, policy-governed agent memory."""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models_memory import OnboardingPrimaryGoal
from app.models_roleplay import RoleplayScenario


class MemoryType(str, Enum):
    """Allowed episodic memory categories."""

    PRACTICE_EXPERIENCE = "practice_experience"
    HELPFUL_STRATEGY = "helpful_strategy"
    PRACTICE_MILESTONE = "practice_milestone"
    SOCIAL_CONTEXT = "social_context"
    RECURRING_PATTERN = "recurring_pattern"


class MemorySourceType(str, Enum):
    """Allowed product sources for an episodic memory."""

    CHAT = "chat"
    ROLEPLAY = "roleplay"
    WORKSHEET = "worksheet"
    EXPOSURE = "exposure"
    SESSION_REVIEW = "session_review"
    USER_CONFIRMED = "user_confirmed"


class MemoryEvidenceType(str, Enum):
    """Evidence strength supporting one persisted memory."""

    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    COMPLETED_PRODUCT_ACTION = "completed_product_action"
    USER_CONFIRMED = "user_confirmed"


class MemoryRecordStatus(str, Enum):
    """Lifecycle states for an episodic memory record."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class MemorySubjectType(str, Enum):
    """Audited durable-memory subject categories."""

    EPISODIC_MEMORY = "episodic_memory"
    THREAD_CHECKPOINT = "thread_checkpoint"
    MEMORY_PROPOSAL = "memory_proposal"


class MemoryEventType(str, Enum):
    """Content-free lifecycle events emitted by repositories."""

    MEMORY_COMMITTED = "memory_committed"
    MEMORY_ARCHIVED = "memory_archived"
    MEMORY_REACTIVATED = "memory_reactivated"
    MEMORY_INACTIVATED = "memory_inactivated"
    MEMORY_SUPERSEDED = "memory_superseded"
    MEMORY_REVOKED = "memory_revoked"
    MEMORY_DELETED = "memory_deleted"
    MEMORY_RETRIEVED = "memory_retrieved"
    CHECKPOINT_UPDATED = "checkpoint_updated"
    PROPOSAL_CREATED = "proposal_created"
    PROPOSAL_CONFIRMED = "proposal_confirmed"
    PROPOSAL_REJECTED = "proposal_rejected"


class PracticeThreadStatus(str, Enum):
    """Lifecycle states for a resumable practice thread."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class MemoryPolicyAction(str, Enum):
    """Deterministic write-policy outcomes."""

    AUTO_COMMIT = "auto_commit"
    REVOKE = "revoke"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REJECT = "reject"


class MemoryPolicyReason(str, Enum):
    """Value-free reason codes for memory write decisions."""

    COMPLETED_PRACTICE_ALLOWED = "completed_practice_allowed"
    HELPFUL_STRATEGY_ALLOWED = "helpful_strategy_allowed"
    SOCIAL_CONTEXT_CONFIRMATION_REQUIRED = "social_context_confirmation_required"
    EXPLICIT_EXPERIENCE_CONFIRMATION_REQUIRED = (
        "explicit_experience_confirmation_required"
    )
    GENERAL_CONSENT_REQUIRED = "general_consent_required"
    CRISIS_CONTENT_REJECTED = "crisis_content_rejected"
    DIAGNOSIS_OR_TRAUMA_INFERENCE_REJECTED = (
        "diagnosis_or_trauma_inference_rejected"
    )
    THIRD_PARTY_OR_IDENTIFIER_REJECTED = "third_party_or_identifier_rejected"
    PROMPT_INJECTION_REJECTED = "prompt_injection_rejected"
    LOW_CONFIDENCE_REJECTED = "low_confidence_rejected"
    EXPLICIT_REVOCATION_ALLOWED = "explicit_revocation_allowed"
    EXPLICIT_REVOCATION_REQUIRED = "explicit_revocation_required"
    REVOCATION_TARGET_NOT_FOUND = "revocation_target_not_found"


class MemoryProposalOperation(str, Enum):
    """Bounded operations a model may propose without choosing record ids."""

    ADD = "add"
    REVOKE = "revoke"


class MemoryProposalStatus(str, Enum):
    """Lifecycle states for confirmation-gated proposals."""

    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryRetrievalStrategy(str, Enum):
    """Deterministic retrieval baselines compared before vector adoption."""

    RECENT = "recent"
    METADATA = "metadata"
    SQL_TEXT = "sql_text"


class EpisodicMemoryRecord(BaseModel):
    """A bounded, source-linked episodic memory safe for durable storage."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    memory_id: str = Field(
        default_factory=lambda: f"memory_{uuid4().hex}",
        min_length=1,
        max_length=128,
    )
    user_id: str = Field(min_length=1, max_length=128)
    memory_type: MemoryType
    summary: str = Field(min_length=1, max_length=500)
    scenario_type: RoleplayScenario | None = None
    source_type: MemorySourceType
    source_id: str | None = Field(default=None, max_length=128)
    evidence_type: MemoryEvidenceType
    confidence: float = Field(ge=0.0, le=1.0)
    status: MemoryRecordStatus = MemoryRecordStatus.ACTIVE
    occurred_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_retrieved_at: datetime | None = None
    expires_at: datetime | None = None
    consent_version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_id: str | None = Field(default=None, max_length=128)
    version: int = Field(default=1, ge=1)

    @field_validator(
        "occurred_at",
        "created_at",
        "updated_at",
        "last_retrieved_at",
        "expires_at",
        mode="after",
    )
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous timestamps at the durable-storage boundary."""
        if value is not None and value.tzinfo is None:
            raise ValueError("durable memory timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_timeline(self) -> "EpisodicMemoryRecord":
        """Reject impossible lifecycle timestamps."""
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.expires_at is not None and self.expires_at <= self.occurred_at:
            raise ValueError("expires_at must be after occurred_at")
        return self


class MemoryProposal(BaseModel):
    """Strict model-produced candidate that cannot carry tenant identity."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(
        default_factory=lambda: f"proposal_{uuid4().hex}",
        min_length=1,
        max_length=128,
    )
    operation: MemoryProposalOperation = MemoryProposalOperation.ADD
    memory_type: MemoryType
    summary: str = Field(min_length=1, max_length=500)
    scenario_type: RoleplayScenario | None = None
    source_type: MemorySourceType
    source_id: str | None = Field(default=None, max_length=128)
    evidence_type: MemoryEvidenceType
    confidence: float = Field(ge=0.0, le=1.0)
    occurred_at: datetime

    @field_validator("occurred_at", mode="after")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous proposal occurrence times."""
        if value.tzinfo is None:
            raise ValueError("memory proposal timestamps must be timezone-aware")
        return value


class MemoryExtractionResponse(BaseModel):
    """Exact JSON envelope accepted from the proposal extraction model."""

    model_config = ConfigDict(extra="forbid")

    proposals: list[MemoryProposal] = Field(default_factory=list, max_length=5)


class MemoryPolicyDecision(BaseModel):
    """Deterministic decision over one validated candidate."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=128)
    action: MemoryPolicyAction
    reason: MemoryPolicyReason
    safe_summary: str | None = Field(default=None, max_length=500)
    detected_categories: list[str] = Field(default_factory=list, max_length=12)


class PendingMemoryProposalRecord(BaseModel):
    """A safe candidate awaiting explicit user confirmation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    memory_type: MemoryType
    summary: str = Field(min_length=1, max_length=500)
    scenario_type: RoleplayScenario | None = None
    source_type: MemorySourceType
    source_id: str | None = Field(default=None, max_length=128)
    evidence_type: MemoryEvidenceType
    confidence: float = Field(ge=0.0, le=1.0)
    occurred_at: datetime
    status: MemoryProposalStatus = MemoryProposalStatus.PENDING_CONFIRMATION
    policy_reason: MemoryPolicyReason
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime

    @field_validator(
        "occurred_at",
        "created_at",
        "updated_at",
        "expires_at",
        mode="after",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous pending-proposal timestamps."""
        if value.tzinfo is None:
            raise ValueError("pending proposal timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_timeline(self) -> "PendingMemoryProposalRecord":
        """Require a useful finite confirmation window."""
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self


class MemoryPipelineItemResult(BaseModel):
    """Content-free processing result for one extracted candidate."""

    proposal_id: str
    action: MemoryPolicyAction
    reason: MemoryPolicyReason
    memory_id: str | None = None
    deduplicated: bool = False


class MemoryPipelineResult(BaseModel):
    """Batch result that distinguishes no candidates from extraction failure."""

    status: Literal[
        "committed",
        "confirmation_required",
        "rejected",
        "no_candidates",
        "skipped",
        "extraction_failed",
        "write_failed",
        "partial_failure",
    ]
    items: list[MemoryPipelineItemResult] = Field(default_factory=list)
    error_category: str | None = None


class MemoryRetrievalRequest(BaseModel):
    """Application-owned retrieval scope; models cannot supply tenant filters."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=1200)
    allowed_memory_types: list[MemoryType] = Field(min_length=1, max_length=5)
    scenario_type: RoleplayScenario | None = None
    include_archived: bool = False
    limit: int = Field(default=3, ge=1, le=3)
    strategy: MemoryRetrievalStrategy = MemoryRetrievalStrategy.SQL_TEXT


class MemoryRetrievalScore(BaseModel):
    """Explainable score components retained without copying query text."""

    model_config = ConfigDict(extra="forbid")

    lexical: float = Field(ge=0.0, le=1.0)
    scenario: float = Field(ge=0.0, le=1.0)
    recency: float = Field(ge=0.0, le=1.0)
    novelty: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    total: float = Field(ge=0.0, le=1.0)


class MemoryRetrievalHit(BaseModel):
    """One bounded durable-memory hit safe for progressive disclosure."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=128)
    memory_type: MemoryType
    summary: str = Field(min_length=1, max_length=500)
    scenario_type: RoleplayScenario | None = None
    status: MemoryRecordStatus
    occurred_at: datetime
    score: MemoryRetrievalScore
    estimated_tokens: int = Field(ge=1)


class MemoryRetrievalDiagnostics(BaseModel):
    """Content-free retrieval diagnostics for traces and evaluations."""

    model_config = ConfigDict(extra="forbid")

    strategy: MemoryRetrievalStrategy
    candidate_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    returned_count: int = Field(ge=0, le=3)
    estimated_tokens: int = Field(ge=0)
    token_budget: int = Field(ge=1)
    abstained: bool
    consent_allowed: bool
    audit_failed: bool = False


class MemoryRetrievalResult(BaseModel):
    """Bounded retrieval output that distinguishes abstention from failure."""

    model_config = ConfigDict(extra="forbid")

    hits: list[MemoryRetrievalHit] = Field(default_factory=list, max_length=3)
    diagnostics: MemoryRetrievalDiagnostics


class MemoryEvent(BaseModel):
    """A content-free audit event for a durable memory subject."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(
        default_factory=lambda: f"memory_event_{uuid4().hex}",
        min_length=1,
        max_length=128,
    )
    user_id: str = Field(min_length=1, max_length=128)
    subject_type: MemorySubjectType
    subject_id: str = Field(min_length=1, max_length=128)
    event_type: MemoryEventType
    from_status: str | None = Field(default=None, max_length=32)
    to_status: str | None = Field(default=None, max_length=32)
    reason_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    subject_version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: None = Field(
        default=None,
        description="Invariant: audit events never duplicate memory content.",
    )

    @field_validator("created_at", mode="after")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous audit timestamps."""
        if value.tzinfo is None:
            raise ValueError("memory event timestamps must be timezone-aware")
        return value


class PracticeThreadCheckpoint(BaseModel):
    """Minimal durable state needed to resume a long-running practice thread."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    thread_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    current_goal: OnboardingPrimaryGoal | None = None
    current_stage: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    current_scenario: RoleplayScenario | None = None
    helpful_strategy_codes: list[str] = Field(default_factory=list, max_length=8)
    attempted_skill_names: list[str] = Field(default_factory=list, max_length=12)
    unresolved_next_step: str | None = Field(default=None, max_length=240)
    status: PracticeThreadStatus = PracticeThreadStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    last_activity_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("helpful_strategy_codes", "attempted_skill_names", mode="after")
    @classmethod
    def validate_codes(cls, values: list[str]) -> list[str]:
        """Keep checkpoint identifiers controlled, unique, and content-free."""
        result: list[str] = []
        for value in values:
            if (
                not value
                or len(value) > 64
                or not value[0].isalpha()
                or any(
                    character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
                    for character in value
                )
            ):
                raise ValueError("checkpoint codes must use lowercase snake_case")
            if value not in result:
                result.append(value)
        return result

    @field_validator(
        "last_activity_at",
        "created_at",
        "updated_at",
        mode="after",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject ambiguous checkpoint timestamps."""
        if value.tzinfo is None:
            raise ValueError("checkpoint timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_timeline(self) -> "PracticeThreadCheckpoint":
        """Reject checkpoints whose update timestamp predates creation."""
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self
