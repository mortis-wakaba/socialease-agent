"""Persistence policy models for privacy-aware writes."""

from enum import Enum
import os

from pydantic import BaseModel, Field


class PersistenceKind(str, Enum):
    """Kinds of user-derived text that may be persisted."""

    TRACE_INPUT = "trace_input"
    TRACE_OUTPUT = "trace_output"
    WORKSHEET_SOURCE_MESSAGE = "worksheet_source_message"
    WORKSHEET_FIELD = "worksheet_field"
    ROLEPLAY_MESSAGE = "roleplay_message"
    ROLEPLAY_AGENT_MESSAGE = "roleplay_agent_message"
    MEMORY_PREFERENCE = "memory_preference"
    ONBOARDING_FIELD = "onboarding_field"
    EXPOSURE_TARGET_SCENARIO = "exposure_target_scenario"
    EXPOSURE_PREVIOUS_ATTEMPT = "exposure_previous_attempt"
    EXPOSURE_REFLECTION = "exposure_reflection"
    SESSION_REVIEW_NEXT_STEP = "session_review_next_step"


class TraceOutputPolicy(str, Enum):
    """Supported persistence strategies for assistant trace output."""

    REDACT_ONLY = "redact_only"
    SUMMARY_ONLY = "summary_only"
    MINIMIZED = "minimized"


RAW_MESSAGE_KINDS = {
    PersistenceKind.TRACE_INPUT,
    PersistenceKind.WORKSHEET_SOURCE_MESSAGE,
    PersistenceKind.ROLEPLAY_MESSAGE,
    PersistenceKind.EXPOSURE_TARGET_SCENARIO,
    PersistenceKind.EXPOSURE_PREVIOUS_ATTEMPT,
    PersistenceKind.EXPOSURE_REFLECTION,
}


class PersistenceDecision(BaseModel):
    """Result of applying persistence policy to one text value."""

    kind: PersistenceKind
    original_length: int
    persisted_text: str
    minimized: bool = False
    summarized: bool = False
    policy: str = "default"
    redacted_types: list[str] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Return whether the text was changed before persistence."""
        return self.minimized or self.summarized or bool(self.redacted_types)


MINIMIZED_TEXT_BY_KIND = {
    PersistenceKind.TRACE_INPUT: "[raw chat input minimized by privacy policy]",
    PersistenceKind.TRACE_OUTPUT: "[assistant output minimized by privacy policy]",
    PersistenceKind.WORKSHEET_SOURCE_MESSAGE: "[raw worksheet source minimized by privacy policy]",
    PersistenceKind.ROLEPLAY_MESSAGE: "[raw roleplay message minimized by privacy policy]",
    PersistenceKind.EXPOSURE_TARGET_SCENARIO: "[raw exposure target scenario minimized by privacy policy]",
    PersistenceKind.EXPOSURE_PREVIOUS_ATTEMPT: "[raw previous attempt minimized by privacy policy]",
    PersistenceKind.EXPOSURE_REFLECTION: "[raw exposure reflection minimized by privacy policy]",
}


def trace_output_policy_from_env() -> TraceOutputPolicy:
    """Return the configured trace-output persistence strategy."""
    configured = os.getenv("SOCIALEASE_TRACE_OUTPUT_MODE", "").strip().lower()
    if configured:
        try:
            return TraceOutputPolicy(configured)
        except ValueError:
            return TraceOutputPolicy.SUMMARY_ONLY
    auth_mode = os.getenv("SOCIALEASE_AUTH_MODE", "demo").strip().lower()
    if auth_mode == "production":
        return TraceOutputPolicy.SUMMARY_ONLY
    return TraceOutputPolicy.REDACT_ONLY
