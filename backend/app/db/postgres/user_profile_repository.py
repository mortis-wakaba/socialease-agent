"""PostgreSQL user-profile summary repository implementation."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models_exposure import ExposurePlan
from app.models_memory import UserPracticeSummary
from app.models_roleplay import RoleplaySession


class PostgresUserProfileRepository:
    """Build privacy-minimized practice summaries from PostgreSQL records."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def get_summary(self, user_id: str) -> UserPracticeSummary:
        """Return an aggregate practice summary for one user."""
        with self.engine.connect() as connection:
            roleplay_rows = connection.execute(
                text(
                    """SELECT payload FROM roleplay_sessions
                    WHERE user_id = :user_id
                    ORDER BY updated_at DESC"""
                ),
                {"user_id": user_id},
            ).mappings().all()
            worksheet_count = connection.execute(
                text("SELECT COUNT(*) AS count FROM worksheets WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).mappings().first()["count"]
            plan_row = connection.execute(
                text("SELECT payload FROM exposure_plans WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).mappings().first()

        sessions = [RoleplaySession.model_validate(row["payload"]) for row in roleplay_rows]
        plan = ExposurePlan.model_validate(plan_row["payload"]) if plan_row else None
        recent_scenarios = list(dict.fromkeys(session.scenario.value for session in sessions))[:3]
        preferred_difficulty = sessions[0].difficulty if sessions else None
        latest_anxiety_level = None
        exposure_attempt_count = 0
        if plan is not None:
            exposure_attempt_count = len(plan.attempts)
            latest_anxiety_level = (
                plan.attempts[-1].anxiety_after
                if plan.attempts
                else plan.current_anxiety_level
            )
            if plan.target_scenario not in recent_scenarios:
                recent_scenarios = [plan.target_scenario, *recent_scenarios][:3]

        return UserPracticeSummary(
            recent_scenarios=recent_scenarios,
            roleplay_session_count=len(sessions),
            worksheet_count=worksheet_count,
            exposure_attempt_count=exposure_attempt_count,
            latest_anxiety_level=latest_anxiety_level,
            preferred_difficulty=preferred_difficulty,
        )
