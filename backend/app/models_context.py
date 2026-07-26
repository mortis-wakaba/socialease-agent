"""Typed models for task-specific, privacy-minimized context selection."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models_memory import (
    OnboardingPracticePreference,
    OnboardingPrimaryGoal,
    PreferredFeedbackStyle,
)


class ContextValueSource(str, Enum):
    """Stable provenance labels for one selected context field."""

    CURRENT_REQUEST = "current_request"
    EXPLICIT_PREFERENCE = "explicit_preference"
    ONBOARDING = "onboarding"
    RECENT_PRACTICE = "recent_practice"
    DEFAULT = "default"


class ContextConfidence(str, Enum):
    """Whether a selected value was explicit, derived, or a default."""

    EXPLICIT = "explicit"
    DERIVED = "derived"
    DEFAULT = "default"


class ContextFieldMetadata(BaseModel):
    """Trace-safe provenance for one context field without retaining its value."""

    model_config = ConfigDict(extra="forbid")

    sources: list[ContextValueSource] = Field(default_factory=list, max_length=4)
    confidence: ContextConfidence
    observed_at: datetime | None = None
    expires_at: datetime | None = None


class SkillContextProjection(BaseModel):
    """A bounded application-owned context packet selected for one skill."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    values: dict[str, Any] = Field(default_factory=dict)
    selected_fields: list[str] = Field(default_factory=list, max_length=16)
    field_metadata: dict[str, ContextFieldMetadata] = Field(default_factory=dict)
    dropped_fields: list[str] = Field(default_factory=list, max_length=24)
    drop_reasons: dict[str, str] = Field(default_factory=dict)
    selected_at: datetime


class SupportGenerationContext(BaseModel):
    """Low-sensitivity preferences allowed in the support-generation prompt."""

    model_config = ConfigDict(extra="forbid")

    primary_goal: OnboardingPrimaryGoal | None = None
    preferred_feedback_style: PreferredFeedbackStyle | None = None
    practice_preference: OnboardingPracticePreference | None = None
    wants_pause_reminders: bool | None = None
