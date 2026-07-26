"""Shared projection from product records to a privacy-minimized profile."""

from collections.abc import Sequence

from app.models_exposure import ExposurePlan
from app.models_memory import UserPracticeSummary
from app.models_roleplay import RoleplaySession


def build_user_practice_summary(
    *,
    sessions: Sequence[RoleplaySession],
    worksheet_count: int,
    exposure_plan: ExposurePlan | None,
) -> UserPracticeSummary:
    """Build one provider-independent aggregate from validated records."""
    recent_scenarios = list(
        dict.fromkeys(session.scenario.value for session in sessions)
    )[:3]
    preferred_difficulty = sessions[0].difficulty if sessions else None
    practice_timestamps = [session.updated_at for session in sessions]
    latest_anxiety_level = None
    exposure_attempt_count = 0
    if exposure_plan is not None:
        practice_timestamps.append(exposure_plan.updated_at)
        exposure_attempt_count = len(exposure_plan.attempts)
        latest_anxiety_level = (
            exposure_plan.attempts[-1].anxiety_after
            if exposure_plan.attempts
            else exposure_plan.current_anxiety_level
        )
        if exposure_plan.target_scenario not in recent_scenarios:
            recent_scenarios = [
                exposure_plan.target_scenario,
                *recent_scenarios,
            ][:3]

    return UserPracticeSummary(
        recent_scenarios=recent_scenarios,
        roleplay_session_count=len(sessions),
        worksheet_count=worksheet_count,
        exposure_attempt_count=exposure_attempt_count,
        latest_anxiety_level=latest_anxiety_level,
        preferred_difficulty=preferred_difficulty,
        latest_practice_at=(
            max(practice_timestamps) if practice_timestamps else None
        ),
    )
