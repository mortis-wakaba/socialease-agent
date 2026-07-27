"""Pydantic models for privacy-minimized user memory summaries."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models_memory_types import MemoryType


MEMORY_PRIVACY_NOTICE = (
    "练习记录与跨会话个性化分开管理；只有在你开启对应用途后，历史练习摘要或"
    "低敏感度偏好才会进入未来对话。不保存诊断结论，也不保存危机原文副本。"
    "你可以随时撤回授权，并导出或删除自己拥有的练习记录。"
)


class UserPracticeSummary(BaseModel):
    """Aggregated recent practice state without sensitive raw-message history."""

    recent_scenarios: list[str] = Field(default_factory=list)
    roleplay_session_count: int = 0
    worksheet_count: int = 0
    exposure_attempt_count: int = 0
    latest_anxiety_level: int | None = Field(default=None, ge=1, le=10)
    preferred_difficulty: int | None = Field(default=None, ge=1, le=5)
    latest_practice_at: datetime | None = Field(default=None, exclude=True)


class UserConsentState(BaseModel):
    """Separate history persistence and long-term agent-memory controls."""

    store_conversation_history: bool = True
    consent_to_practice_summary: bool = False
    consent_to_save_preferences: bool = False
    do_not_store_raw_messages: bool = True
    allow_sensitive_memory: bool = False


AgentMemoryType = MemoryType


class PreferredFeedbackStyle(str, Enum):
    """Controlled low-sensitivity feedback style codes."""

    GENTLE_SPECIFIC = "gentle_specific"
    BRIEF_ACTIONABLE = "brief_actionable"
    ENCOURAGING_REFLECTIVE = "encouraging_reflective"


class OnboardingPrimaryGoal(str, Enum):
    """Controlled onboarding goal codes."""

    CLEARER_CLASSROOM_EXPRESSION = "clearer_classroom_expression"
    STEADIER_GROUP_OR_DORM_COMMUNICATION = "steadier_group_or_dorm_communication"
    BOUNDARY_AND_REFUSAL_PRACTICE = "boundary_and_refusal_practice"
    INTERVIEW_SELF_INTRO_CONFIDENCE = "interview_self_intro_confidence"


class OnboardingPracticePreference(str, Enum):
    """Controlled onboarding practice preference codes."""

    SHORT_SENTENCE_FIRST = "short_sentence_first"
    STEP_BY_STEP_LADDER = "step_by_step_ladder"
    ROLEPLAY_THEN_FEEDBACK = "roleplay_then_feedback"


class PracticePreferences(BaseModel):
    """Low-sensitivity practice preferences for future personalization."""

    preferred_roleplay_difficulty: int | None = Field(default=None, ge=1, le=5)
    preferred_feedback_style: PreferredFeedbackStyle | None = None
    preferred_practice_scenarios: list[str] = Field(default_factory=list, max_length=5)


class UserOnboardingProfile(BaseModel):
    """Low-sensitivity onboarding choices that can guide future practice."""

    primary_goal: OnboardingPrimaryGoal | None = None
    preferred_scenario: str | None = Field(default=None, max_length=240)
    current_anxiety_level: int | None = Field(default=None, ge=1, le=10)
    practice_preference: OnboardingPracticePreference | None = None
    wants_pause_reminders: bool = True
    wants_auto_review: bool = True
    boundary_acknowledged: bool = False


class MemoryContext(BaseModel):
    """Privacy-safe memory packet injected into one agent run."""

    recent_scenarios: list[str] = Field(default_factory=list, max_length=5)
    preferred_difficulty: int | None = Field(default=None, ge=1, le=5)
    latest_anxiety_level: int | None = Field(default=None, ge=1, le=10)
    practice_preferences: PracticePreferences = Field(default_factory=PracticePreferences)
    onboarding_profile: UserOnboardingProfile = Field(default_factory=UserOnboardingProfile)
    practice_summary_observed_at: datetime | None = None
    practice_summary_expires_at: datetime | None = None
    dropped_context: list[str] = Field(default_factory=list, max_length=8)


class UserMemorySettings(BaseModel):
    """Persisted privacy-aware memory settings for one user."""

    consent_state: UserConsentState = Field(default_factory=UserConsentState)
    practice_preferences: PracticePreferences = Field(default_factory=PracticePreferences)
    onboarding_profile: UserOnboardingProfile = Field(default_factory=UserOnboardingProfile)
    disabled_memory_types: list[AgentMemoryType] = Field(
        default_factory=list,
        max_length=5,
    )


class MemoryTypePersonalizationRequest(BaseModel):
    """Enable or disable one memory category for future personalization."""

    enabled: bool


class MemoryTypePersonalizationResponse(BaseModel):
    """Current disabled-category set after one user control change."""

    user_id: str
    memory_type: AgentMemoryType
    enabled: bool
    disabled_memory_types: list[AgentMemoryType] = Field(
        default_factory=list,
        max_length=5,
    )


class UserOnboardingProfileUpdateRequest(BaseModel):
    """Request to save low-sensitivity onboarding profile fields."""

    onboarding_profile: UserOnboardingProfile


class UserOnboardingProfileResponse(BaseModel):
    """Response returned for onboarding profile reads and writes."""

    user_id: str
    onboarding_profile: UserOnboardingProfile


class MemoryPreferencesUpdateRequest(BaseModel):
    """Request to save low-sensitivity practice preferences with explicit consent."""

    consent_to_save_preferences: bool
    practice_preferences: PracticePreferences


class MemoryPreferencesUpdateResponse(BaseModel):
    """Response returned after updating memory preferences."""

    user_id: str
    consent_state: UserConsentState
    practice_preferences: PracticePreferences


class PracticeSummaryConsentUpdateRequest(BaseModel):
    """User choice for using saved practice summaries in future agent runs."""

    consent_to_practice_summary: bool


class PracticeSummaryConsentUpdateResponse(BaseModel):
    """Response returned after changing practice-summary personalization consent."""

    user_id: str
    consent_state: UserConsentState


class UserMemoryExportResponse(BaseModel):
    """Exportable user-owned memory records."""

    user_id: str
    profile: "UserProfileResponse"
    records: dict[str, list[dict[str, object]]] = Field(default_factory=dict)


class UserMemoryDeleteResponse(BaseModel):
    """Deletion result for user-owned memory records."""

    user_id: str
    deleted_counts: dict[str, int] = Field(default_factory=dict)
    profile_after_delete: "UserProfileResponse"


class UserProfileResponse(BaseModel):
    """Response returned for one user's lightweight profile."""

    user_id: str
    practice_summary: UserPracticeSummary
    consent_state: UserConsentState = Field(default_factory=UserConsentState)
    practice_preferences: PracticePreferences = Field(default_factory=PracticePreferences)
    privacy_notice: str = MEMORY_PRIVACY_NOTICE
    memory_export_available: bool = True
    memory_delete_available: bool = True
    deletion_endpoint_reserved: bool = Field(
        default=False,
        deprecated=True,
        description="Deprecated compatibility field. Use memory_delete_available.",
    )
    export_endpoint_reserved: bool = Field(
        default=False,
        deprecated=True,
        description="Deprecated compatibility field. Use memory_export_available.",
    )
