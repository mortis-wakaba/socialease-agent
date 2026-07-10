"""Build privacy-safe runtime memory context for the agent harness."""

from app.models_exposure import ExposurePlan
from app.models_memory import (
    MemoryContext,
    PracticePreferences,
    UserMemorySettings,
    UserPracticeSummary,
)
from app.privacy.redaction import redact_sensitive_identifiers


def build_memory_context(
    *,
    practice_summary: UserPracticeSummary,
    memory_settings: UserMemorySettings,
    active_exposure_plan: ExposurePlan | None,
) -> MemoryContext:
    """Return a bounded memory packet for one agent run."""
    preferences = _redact_preferences(memory_settings.practice_preferences)
    recent_scenarios = _dedupe(
        [
            *preferences.preferred_practice_scenarios,
            *[_redact_text(value) for value in practice_summary.recent_scenarios],
        ],
        limit=5,
    )
    preferred_difficulty = (
        preferences.preferred_roleplay_difficulty
        if preferences.preferred_roleplay_difficulty is not None
        else practice_summary.preferred_difficulty
    )
    latest_anxiety_level = practice_summary.latest_anxiety_level
    onboarding_profile = memory_settings.onboarding_profile
    context_notes: list[str] = []

    if recent_scenarios:
        context_notes.append("recent_practice_scenarios_available")
    if preferred_difficulty is not None:
        context_notes.append("preferred_roleplay_difficulty_available")
    if latest_anxiety_level is not None:
        context_notes.append("latest_anxiety_level_available")
    if active_exposure_plan is not None:
        context_notes.append("active_exposure_plan_available")
    if onboarding_profile.boundary_acknowledged:
        context_notes.append("onboarding_profile_available")

    return MemoryContext(
        recent_scenarios=recent_scenarios,
        preferred_difficulty=preferred_difficulty,
        latest_anxiety_level=latest_anxiety_level,
        active_exposure_plan_id=active_exposure_plan.plan_id if active_exposure_plan else None,
        active_exposure_next_task=_next_exposure_task_title(active_exposure_plan),
        practice_preferences=preferences,
        onboarding_profile=onboarding_profile,
        context_notes=context_notes,
    )


def _dedupe(values: list[str], *, limit: int) -> list[str]:
    """Return trimmed unique non-empty values in stable order."""
    result: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _redact_preferences(preferences: PracticePreferences) -> PracticePreferences:
    """Return preferences safe for runtime context injection."""
    return PracticePreferences(
        preferred_roleplay_difficulty=preferences.preferred_roleplay_difficulty,
        preferred_feedback_style=(
            _redact_text(preferences.preferred_feedback_style)
            if preferences.preferred_feedback_style is not None
            else None
        ),
        preferred_practice_scenarios=[
            _redact_text(value) for value in preferences.preferred_practice_scenarios
        ],
    )


def _redact_text(text: str) -> str:
    """Redact deterministic sensitive identifiers before context injection."""
    redacted, _ = redact_sensitive_identifiers(text)
    return redacted


def _next_exposure_task_title(plan: ExposurePlan | None) -> str | None:
    """Return the active plan's recommended next task title, if available."""
    if plan is None or plan.recommended_next_task_id is None:
        return None
    for task in plan.tasks:
        if task.task_id == plan.recommended_next_task_id:
            return task.title
    return None
