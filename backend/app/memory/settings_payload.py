"""Compatibility loader for privacy-aware memory settings payloads."""

import json
from typing import Any

from pydantic import ValidationError

from app.models_memory import (
    OnboardingPracticePreference,
    OnboardingPrimaryGoal,
    PracticePreferences,
    PreferredFeedbackStyle,
    UserConsentState,
    UserMemorySettings,
    UserOnboardingProfile,
)
from app.models_roleplay import RoleplayScenario


FEEDBACK_STYLE_ALIASES: dict[str, PreferredFeedbackStyle] = {
    "简洁直接": PreferredFeedbackStyle.BRIEF_ACTIONABLE,
    "简短可执行": PreferredFeedbackStyle.BRIEF_ACTIONABLE,
    "简短、可执行": PreferredFeedbackStyle.BRIEF_ACTIONABLE,
    "简短，可执行": PreferredFeedbackStyle.BRIEF_ACTIONABLE,
    "简短行动导向": PreferredFeedbackStyle.BRIEF_ACTIONABLE,
    "简短、行动导向": PreferredFeedbackStyle.BRIEF_ACTIONABLE,
    "温和具体": PreferredFeedbackStyle.GENTLE_SPECIFIC,
    "温和、具体": PreferredFeedbackStyle.GENTLE_SPECIFIC,
    "温和、具体、可执行": PreferredFeedbackStyle.GENTLE_SPECIFIC,
    "温和具体可执行": PreferredFeedbackStyle.GENTLE_SPECIFIC,
    "鼓励反思": PreferredFeedbackStyle.ENCOURAGING_REFLECTIVE,
    "鼓励反思型": PreferredFeedbackStyle.ENCOURAGING_REFLECTIVE,
    "鼓励式带一点反思": PreferredFeedbackStyle.ENCOURAGING_REFLECTIVE,
    "鼓励式、带一点反思": PreferredFeedbackStyle.ENCOURAGING_REFLECTIVE,
}

PRIMARY_GOAL_ALIASES: dict[str, OnboardingPrimaryGoal] = {
    "课堂表达": OnboardingPrimaryGoal.CLEARER_CLASSROOM_EXPRESSION,
    "课堂发言": OnboardingPrimaryGoal.CLEARER_CLASSROOM_EXPRESSION,
    "宿舍沟通": OnboardingPrimaryGoal.STEADIER_GROUP_OR_DORM_COMMUNICATION,
    "小组沟通": OnboardingPrimaryGoal.STEADIER_GROUP_OR_DORM_COMMUNICATION,
    "边界拒绝": OnboardingPrimaryGoal.BOUNDARY_AND_REFUSAL_PRACTICE,
    "拒绝别人": OnboardingPrimaryGoal.BOUNDARY_AND_REFUSAL_PRACTICE,
    "面试自我介绍": OnboardingPrimaryGoal.INTERVIEW_SELF_INTRO_CONFIDENCE,
}

PRACTICE_PREFERENCE_ALIASES: dict[str, OnboardingPracticePreference] = {
    "短句先练": OnboardingPracticePreference.SHORT_SENTENCE_FIRST,
    "先练短句": OnboardingPracticePreference.SHORT_SENTENCE_FIRST,
    "分级练习": OnboardingPracticePreference.STEP_BY_STEP_LADDER,
    "阶梯练习": OnboardingPracticePreference.STEP_BY_STEP_LADDER,
    "角色扮演后反馈": OnboardingPracticePreference.ROLEPLAY_THEN_FEEDBACK,
    "先角色扮演再反馈": OnboardingPracticePreference.ROLEPLAY_THEN_FEEDBACK,
}


def load_user_memory_settings_payload(payload: str | dict[str, Any] | None) -> UserMemorySettings:
    """Load memory settings with schema-evolution sanitization on invalid payloads."""
    if not payload:
        return UserMemorySettings()
    parsed = _parse_payload(payload)
    try:
        return UserMemorySettings.model_validate(parsed)
    except ValidationError:
        return _sanitize_settings_payload(parsed)


def _parse_payload(payload: str | dict[str, Any]) -> dict[str, Any]:
    """Parse a SQLite JSON string or PostgreSQL JSON object into a dict."""
    if isinstance(payload, dict):
        return payload
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize_settings_payload(payload: dict[str, Any]) -> UserMemorySettings:
    """Keep only allowed low-sensitivity memory fields from invalid payloads."""
    return UserMemorySettings(
        consent_state=_sanitize_consent_state(payload.get("consent_state")),
        practice_preferences=_sanitize_practice_preferences(
            payload.get("practice_preferences")
        ),
        onboarding_profile=_sanitize_onboarding_profile(payload.get("onboarding_profile")),
    )


def _sanitize_consent_state(value: Any) -> UserConsentState:
    """Sanitize consent flags, keeping only booleans."""
    if not isinstance(value, dict):
        return UserConsentState()
    return UserConsentState(
        consent_to_practice_summary=_bool_or_default(
            value.get("consent_to_practice_summary"), False
        ),
        consent_to_save_preferences=_bool_or_default(
            value.get("consent_to_save_preferences"), False
        ),
        do_not_store_raw_messages=_bool_or_default(
            value.get("do_not_store_raw_messages"), True
        ),
        allow_sensitive_memory=_bool_or_default(value.get("allow_sensitive_memory"), False),
    )


def _sanitize_practice_preferences(value: Any) -> PracticePreferences:
    """Sanitize practice preferences without preserving arbitrary free text."""
    if not isinstance(value, dict):
        return PracticePreferences()
    return PracticePreferences(
        preferred_roleplay_difficulty=_int_in_range(
            value.get("preferred_roleplay_difficulty"), minimum=1, maximum=5
        ),
        preferred_feedback_style=_enum_or_alias(
            value.get("preferred_feedback_style"),
            PreferredFeedbackStyle,
            FEEDBACK_STYLE_ALIASES,
        ),
        preferred_practice_scenarios=_enum_list(
            value.get("preferred_practice_scenarios"),
            RoleplayScenario,
            max_items=5,
        ),
    )


def _sanitize_onboarding_profile(value: Any) -> UserOnboardingProfile:
    """Sanitize onboarding choices without preserving arbitrary free text."""
    if not isinstance(value, dict):
        return UserOnboardingProfile()
    return UserOnboardingProfile(
        primary_goal=_enum_or_alias(
            value.get("primary_goal"),
            OnboardingPrimaryGoal,
            PRIMARY_GOAL_ALIASES,
        ),
        preferred_scenario=_enum_or_none(value.get("preferred_scenario"), RoleplayScenario),
        current_anxiety_level=_int_in_range(
            value.get("current_anxiety_level"), minimum=1, maximum=10
        ),
        practice_preference=_enum_or_alias(
            value.get("practice_preference"),
            OnboardingPracticePreference,
            PRACTICE_PREFERENCE_ALIASES,
        ),
        wants_pause_reminders=_bool_or_default(
            value.get("wants_pause_reminders"), True
        ),
        wants_auto_review=_bool_or_default(value.get("wants_auto_review"), True),
        boundary_acknowledged=_bool_or_default(
            value.get("boundary_acknowledged"), False
        ),
    )


def _bool_or_default(value: Any, default: bool) -> bool:
    """Return a boolean only when the payload already contains a boolean."""
    return value if isinstance(value, bool) else default


def _int_in_range(value: Any, *, minimum: int, maximum: int) -> int | None:
    """Return an int only when it is inside the allowed range."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value if minimum <= value <= maximum else None


def _enum_or_none(value: Any, enum_type: type) -> Any | None:
    """Return an enum value only when the raw value is a known enum member."""
    if not isinstance(value, str):
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _enum_or_alias(value: Any, enum_type: type, aliases: dict[str, Any]) -> Any | None:
    """Return a known enum value or a curated alias; drop arbitrary text."""
    if not isinstance(value, str):
        return None
    normalized = _normalize_label(value)
    direct = _enum_or_none(normalized, enum_type)
    if direct is not None:
        return direct
    return aliases.get(normalized)


def _enum_list(value: Any, enum_type: type, *, max_items: int) -> list[Any]:
    """Return known enum values from a list while preserving order and uniqueness."""
    if not isinstance(value, list):
        return []
    sanitized: list[Any] = []
    seen: set[str] = set()
    for item in value:
        enum_value = _enum_or_none(item, enum_type)
        if enum_value is None or enum_value.value in seen:
            continue
        sanitized.append(enum_value)
        seen.add(enum_value.value)
        if len(sanitized) >= max_items:
            break
    return sanitized


def _normalize_label(value: str) -> str:
    """Normalize curated legacy labels without preserving arbitrary free text."""
    return (
        value.strip()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("，", "、")
        .replace(",", "、")
        .replace("/", "、")
        .replace("／", "、")
        .replace("-", "")
        .replace("—", "")
    )
