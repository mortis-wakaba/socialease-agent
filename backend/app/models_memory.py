"""Pydantic models for privacy-minimized user memory summaries."""

from pydantic import BaseModel, Field


MEMORY_PRIVACY_NOTICE = (
    "Demo summary only: stores lightweight practice state and preferences, not diagnoses "
    "or unnecessary crisis-text copies. A deletion endpoint is reserved for a later milestone."
)


class UserPracticeSummary(BaseModel):
    """Aggregated recent practice state without sensitive raw-message history."""

    recent_scenarios: list[str] = Field(default_factory=list)
    roleplay_session_count: int = 0
    worksheet_count: int = 0
    exposure_attempt_count: int = 0
    latest_anxiety_level: int | None = Field(default=None, ge=1, le=10)
    preferred_difficulty: int | None = Field(default=None, ge=1, le=5)


class UserProfileResponse(BaseModel):
    """Response returned for one user's lightweight profile."""

    user_id: str
    practice_summary: UserPracticeSummary
    privacy_notice: str = MEMORY_PRIVACY_NOTICE
    deletion_endpoint_reserved: bool = True
