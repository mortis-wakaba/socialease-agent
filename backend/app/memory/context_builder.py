"""Build privacy-safe runtime memory context for the agent harness."""

from datetime import datetime, timedelta, timezone

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
    now: datetime | None = None,
    practice_summary_ttl: timedelta = timedelta(days=90),
    active_plan_ttl: timedelta = timedelta(days=30),
) -> MemoryContext:
    """Return a bounded memory packet for one agent run."""
    selected_at = _as_utc(now or datetime.now(timezone.utc))
    practice_observed_at = _as_utc_or_none(practice_summary.latest_practice_at)
    practice_expires_at = _expiry(practice_observed_at, practice_summary_ttl)
    practice_summary_stale = _is_expired(practice_expires_at, selected_at)
    active_plan_updated_at = _as_utc_or_none(
        active_exposure_plan.updated_at if active_exposure_plan is not None else None
    )
    active_plan_expires_at = _expiry(active_plan_updated_at, active_plan_ttl)
    active_plan_stale = _is_expired(active_plan_expires_at, selected_at)
    usable_active_plan = None if active_plan_stale else active_exposure_plan

    preferences = _redact_preferences(memory_settings.practice_preferences)
    recent_scenarios = _dedupe(
        [
            *preferences.preferred_practice_scenarios,
            *(
                []
                if practice_summary_stale
                else [_redact_text(value) for value in practice_summary.recent_scenarios]
            ),
        ],
        limit=5,
    )
    preferred_difficulty = (
        preferences.preferred_roleplay_difficulty
        if preferences.preferred_roleplay_difficulty is not None
        else None if practice_summary_stale else practice_summary.preferred_difficulty
    )
    latest_anxiety_level = (
        None if practice_summary_stale else practice_summary.latest_anxiety_level
    )
    onboarding_profile = memory_settings.onboarding_profile
    context_notes: list[str] = []
    dropped_context: list[str] = []

    if practice_summary_stale:
        dropped_context.append("practice_summary_expired")
    if active_plan_stale:
        dropped_context.append("active_exposure_plan_expired")

    if recent_scenarios:
        context_notes.append("recent_practice_scenarios_available")
    if preferred_difficulty is not None:
        context_notes.append("preferred_roleplay_difficulty_available")
    if latest_anxiety_level is not None:
        context_notes.append("latest_anxiety_level_available")
    if usable_active_plan is not None:
        context_notes.append("active_exposure_plan_available")
    if onboarding_profile.boundary_acknowledged:
        context_notes.append("onboarding_profile_available")

    return MemoryContext(
        recent_scenarios=recent_scenarios,
        preferred_difficulty=preferred_difficulty,
        latest_anxiety_level=latest_anxiety_level,
        active_exposure_plan_id=usable_active_plan.plan_id if usable_active_plan else None,
        active_exposure_next_task=_next_exposure_task_title(usable_active_plan),
        practice_preferences=preferences,
        onboarding_profile=onboarding_profile,
        context_notes=context_notes,
        practice_summary_observed_at=practice_observed_at,
        practice_summary_expires_at=practice_expires_at,
        active_exposure_plan_updated_at=active_plan_updated_at,
        active_exposure_plan_expires_at=active_plan_expires_at,
        dropped_context=dropped_context,
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
    """Copy validated, enum-backed preferences into the runtime context."""
    return preferences.model_copy(deep=True)


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


def _expiry(observed_at: datetime | None, ttl: timedelta) -> datetime | None:
    """Return the UTC expiration time for one timestamped context source."""
    return observed_at + ttl if observed_at is not None else None


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    """Treat timestamped context as stale only after its configured expiry."""
    return expires_at is not None and expires_at < now


def _as_utc_or_none(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    """Normalize legacy naive timestamps for deterministic comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
