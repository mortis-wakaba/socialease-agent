"""Select the minimum validated context fields needed by each skill."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeVar

from app.models_context import (
    ContextConfidence,
    ContextFieldMetadata,
    ContextValueSource,
    SkillContextProjection,
)
from app.models_memory import (
    MemoryContext,
    OnboardingPracticePreference,
    OnboardingPrimaryGoal,
    PreferredFeedbackStyle,
)
from app.models_roleplay import RoleplayScenario
from app.privacy.redaction import redact_sensitive_identifiers


EnumT = TypeVar("EnumT", bound=Enum)


def select_skill_context(
    *,
    skill_name: str,
    request_context: dict[str, Any],
    memory_context: MemoryContext | None,
    selected_at: datetime | None = None,
) -> SkillContextProjection:
    """Return one task-specific context projection without arbitrary raw memory."""
    timestamp = selected_at or datetime.now(timezone.utc)
    if skill_name == "general_support_skill":
        return _select_support_context(
            request_context=request_context,
            memory_context=memory_context,
            selected_at=timestamp,
        )
    if skill_name == "roleplay_skill":
        return _select_roleplay_context(
            request_context=request_context,
            memory_context=memory_context,
            selected_at=timestamp,
        )
    if skill_name == "exposure_planning_skill":
        return _select_exposure_context(
            request_context=request_context,
            memory_context=memory_context,
            selected_at=timestamp,
        )
    return SkillContextProjection(skill_name=skill_name, selected_at=timestamp)


def _select_support_context(
    *,
    request_context: dict[str, Any],
    memory_context: MemoryContext | None,
    selected_at: datetime,
) -> SkillContextProjection:
    values: dict[str, object] = {}
    metadata: dict[str, ContextFieldMetadata] = {}
    drop_reasons = _memory_drop_reasons(memory_context)

    _select_enum_field(
        key="primary_goal",
        enum_type=OnboardingPrimaryGoal,
        request_context=request_context,
        stored_value=(
            memory_context.onboarding_profile.primary_goal
            if memory_context is not None
            else None
        ),
        stored_source=ContextValueSource.ONBOARDING,
        values=values,
        metadata=metadata,
        drop_reasons=drop_reasons,
    )
    _select_enum_field(
        key="preferred_feedback_style",
        enum_type=PreferredFeedbackStyle,
        request_context=request_context,
        stored_value=(
            memory_context.practice_preferences.preferred_feedback_style
            if memory_context is not None
            else None
        ),
        stored_source=ContextValueSource.EXPLICIT_PREFERENCE,
        values=values,
        metadata=metadata,
        drop_reasons=drop_reasons,
    )
    _select_enum_field(
        key="practice_preference",
        enum_type=OnboardingPracticePreference,
        request_context=request_context,
        stored_value=(
            memory_context.onboarding_profile.practice_preference
            if memory_context is not None
            else None
        ),
        stored_source=ContextValueSource.ONBOARDING,
        values=values,
        metadata=metadata,
        drop_reasons=drop_reasons,
    )

    raw_pause = request_context.get("wants_pause_reminders")
    if isinstance(raw_pause, bool):
        values["wants_pause_reminders"] = raw_pause
        metadata["wants_pause_reminders"] = _field_metadata(
            ContextValueSource.CURRENT_REQUEST,
            ContextConfidence.EXPLICIT,
        )
    elif raw_pause is not None:
        drop_reasons["wants_pause_reminders"] = "invalid_current_request_type"
    if (
        "wants_pause_reminders" not in values
        and memory_context is not None
        and memory_context.onboarding_profile.boundary_acknowledged
    ):
        values["wants_pause_reminders"] = (
            memory_context.onboarding_profile.wants_pause_reminders
        )
        metadata["wants_pause_reminders"] = _field_metadata(
            ContextValueSource.ONBOARDING,
            ContextConfidence.EXPLICIT,
        )

    available_memory_fields = _available_memory_fields(memory_context)
    selected_fields = sorted(values)
    irrelevant = sorted(
        available_memory_fields
        - {
            "primary_goal",
            "preferred_feedback_style",
            "practice_preference",
            "wants_pause_reminders",
        }
    )
    for field in irrelevant:
        drop_reasons.setdefault(field, "not_relevant_to_skill")
    return SkillContextProjection(
        skill_name="general_support_skill",
        values=values,
        selected_fields=selected_fields,
        field_metadata=metadata,
        dropped_fields=sorted(drop_reasons),
        drop_reasons=drop_reasons,
        selected_at=selected_at,
    )


def _select_roleplay_context(
    *,
    request_context: dict[str, Any],
    memory_context: MemoryContext | None,
    selected_at: datetime,
) -> SkillContextProjection:
    values: dict[str, object] = {}
    metadata: dict[str, ContextFieldMetadata] = {}
    drop_reasons = _memory_drop_reasons(memory_context)

    raw_scenario = request_context.get("scenario")
    scenario = _validated_enum(raw_scenario, RoleplayScenario)
    if scenario is not None:
        values["scenario"] = scenario.value
        metadata["scenario"] = _field_metadata(
            ContextValueSource.CURRENT_REQUEST,
            ContextConfidence.EXPLICIT,
        )
    elif raw_scenario is not None:
        drop_reasons["scenario"] = "invalid_current_request_value"
    elif memory_context is not None and memory_context.recent_scenarios:
        values["recent_scenarios"] = memory_context.recent_scenarios[:5]
        sources: list[ContextValueSource] = []
        if memory_context.practice_preferences.preferred_practice_scenarios:
            sources.append(ContextValueSource.EXPLICIT_PREFERENCE)
        if memory_context.practice_summary_observed_at is not None:
            sources.append(ContextValueSource.RECENT_PRACTICE)
        if not sources:
            sources.append(ContextValueSource.RECENT_PRACTICE)
        metadata["recent_scenarios"] = ContextFieldMetadata(
            sources=list(dict.fromkeys(sources)),
            confidence=ContextConfidence.DERIVED,
            observed_at=memory_context.practice_summary_observed_at,
            expires_at=memory_context.practice_summary_expires_at,
        )

    raw_difficulty = request_context.get("difficulty")
    if _valid_int(raw_difficulty, minimum=1, maximum=5):
        values["preferred_difficulty"] = raw_difficulty
        metadata["preferred_difficulty"] = _field_metadata(
            ContextValueSource.CURRENT_REQUEST,
            ContextConfidence.EXPLICIT,
        )
    elif raw_difficulty is not None:
        drop_reasons["preferred_difficulty"] = "invalid_current_request_value"
    elif memory_context is not None and memory_context.preferred_difficulty is not None:
        values["preferred_difficulty"] = memory_context.preferred_difficulty
        explicit = (
            memory_context.practice_preferences.preferred_roleplay_difficulty
            is not None
        )
        metadata["preferred_difficulty"] = ContextFieldMetadata(
            sources=[
                ContextValueSource.EXPLICIT_PREFERENCE
                if explicit
                else ContextValueSource.RECENT_PRACTICE
            ],
            confidence=(
                ContextConfidence.EXPLICIT if explicit else ContextConfidence.DERIVED
            ),
            observed_at=(
                None if explicit else memory_context.practice_summary_observed_at
            ),
            expires_at=(
                None if explicit else memory_context.practice_summary_expires_at
            ),
        )

    selected_fields = sorted(values)
    relevant = {"scenario", "recent_scenarios", "preferred_difficulty"}
    for field in sorted(_available_memory_fields(memory_context) - relevant):
        drop_reasons.setdefault(field, "not_relevant_to_skill")
    return SkillContextProjection(
        skill_name="roleplay_skill",
        values=values,
        selected_fields=selected_fields,
        field_metadata=metadata,
        dropped_fields=sorted(drop_reasons),
        drop_reasons=drop_reasons,
        selected_at=selected_at,
    )


def _select_exposure_context(
    *,
    request_context: dict[str, Any],
    memory_context: MemoryContext | None,
    selected_at: datetime,
) -> SkillContextProjection:
    values: dict[str, object] = {}
    metadata: dict[str, ContextFieldMetadata] = {}
    drop_reasons = _memory_drop_reasons(memory_context)

    raw_anxiety = request_context.get("current_anxiety_level")
    if _valid_int(raw_anxiety, minimum=1, maximum=10):
        values["current_anxiety_level"] = raw_anxiety
        metadata["current_anxiety_level"] = _field_metadata(
            ContextValueSource.CURRENT_REQUEST,
            ContextConfidence.EXPLICIT,
        )
    elif raw_anxiety is not None:
        drop_reasons["current_anxiety_level"] = "invalid_current_request_value"
    elif memory_context is not None and memory_context.latest_anxiety_level is not None:
        values["current_anxiety_level"] = memory_context.latest_anxiety_level
        metadata["current_anxiety_level"] = ContextFieldMetadata(
            sources=[ContextValueSource.RECENT_PRACTICE],
            confidence=ContextConfidence.DERIVED,
            observed_at=memory_context.practice_summary_observed_at,
            expires_at=memory_context.practice_summary_expires_at,
        )

    raw_target = request_context.get("target_scenario")
    if isinstance(raw_target, str) and raw_target.strip():
        safe_target = redact_sensitive_identifiers(raw_target.strip()[:160])[0]
        values["target_scenario"] = safe_target
        metadata["target_scenario"] = _field_metadata(
            ContextValueSource.CURRENT_REQUEST,
            ContextConfidence.EXPLICIT,
        )
    elif raw_target is not None:
        drop_reasons["target_scenario"] = "invalid_current_request_type"
    elif memory_context is not None:
        if memory_context.recent_scenarios:
            values["recent_scenarios"] = memory_context.recent_scenarios[:5]
            sources: list[ContextValueSource] = []
            if memory_context.practice_preferences.preferred_practice_scenarios:
                sources.append(ContextValueSource.EXPLICIT_PREFERENCE)
            if memory_context.practice_summary_observed_at is not None:
                sources.append(ContextValueSource.RECENT_PRACTICE)
            if not sources:
                sources.append(ContextValueSource.RECENT_PRACTICE)
            metadata["recent_scenarios"] = ContextFieldMetadata(
                sources=sources,
                confidence=ContextConfidence.DERIVED,
                observed_at=memory_context.practice_summary_observed_at,
                expires_at=memory_context.practice_summary_expires_at,
            )
        elif memory_context.onboarding_profile.preferred_scenario is not None:
            values["preferred_scenario"] = (
                memory_context.onboarding_profile.preferred_scenario.value
            )
            metadata["preferred_scenario"] = _field_metadata(
                ContextValueSource.ONBOARDING,
                ContextConfidence.EXPLICIT,
            )

    selected_fields = sorted(values)
    relevant = {
        "current_anxiety_level",
        "target_scenario",
        "recent_scenarios",
        "preferred_scenario",
    }
    for field in sorted(_available_memory_fields(memory_context) - relevant):
        drop_reasons.setdefault(field, "not_relevant_to_skill")
    return SkillContextProjection(
        skill_name="exposure_planning_skill",
        values=values,
        selected_fields=selected_fields,
        field_metadata=metadata,
        dropped_fields=sorted(drop_reasons),
        drop_reasons=drop_reasons,
        selected_at=selected_at,
    )


def _select_enum_field(
    *,
    key: str,
    enum_type: type[EnumT],
    request_context: dict[str, Any],
    stored_value: EnumT | None,
    stored_source: ContextValueSource,
    values: dict[str, object],
    metadata: dict[str, ContextFieldMetadata],
    drop_reasons: dict[str, str],
) -> None:
    raw = request_context.get(key)
    current = _validated_enum(raw, enum_type)
    if current is not None:
        values[key] = current.value
        metadata[key] = _field_metadata(
            ContextValueSource.CURRENT_REQUEST,
            ContextConfidence.EXPLICIT,
        )
        return
    if raw is not None:
        drop_reasons[key] = "invalid_current_request_value"
    if stored_value is not None:
        values[key] = stored_value.value
        metadata[key] = _field_metadata(
            stored_source,
            ContextConfidence.EXPLICIT,
        )


def _validated_enum(value: object, enum_type: type[EnumT]) -> EnumT | None:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _field_metadata(
    source: ContextValueSource,
    confidence: ContextConfidence,
) -> ContextFieldMetadata:
    return ContextFieldMetadata(sources=[source], confidence=confidence)


def _available_memory_fields(memory_context: MemoryContext | None) -> set[str]:
    if memory_context is None:
        return set()
    fields: set[str] = set()
    if memory_context.recent_scenarios:
        fields.add("recent_scenarios")
    if memory_context.preferred_difficulty is not None:
        fields.add("preferred_difficulty")
    if memory_context.latest_anxiety_level is not None:
        fields.add("latest_anxiety_level")
    if memory_context.active_exposure_plan_id is not None:
        fields.add("active_exposure_plan_id")
    if memory_context.active_exposure_next_task is not None:
        fields.add("active_exposure_next_task")
    preferences = memory_context.practice_preferences
    if preferences.preferred_feedback_style is not None:
        fields.add("preferred_feedback_style")
    onboarding = memory_context.onboarding_profile
    if onboarding.primary_goal is not None:
        fields.add("primary_goal")
    if onboarding.practice_preference is not None:
        fields.add("practice_preference")
    if onboarding.boundary_acknowledged:
        fields.add("wants_pause_reminders")
    return fields


def _memory_drop_reasons(memory_context: MemoryContext | None) -> dict[str, str]:
    """Carry source-level expiry decisions into task-level diagnostics."""
    if memory_context is None:
        return {}
    return {item: item for item in memory_context.dropped_context}
