"""PostgreSQL user-profile summary repository implementation."""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_engine
from app.models_exposure import ExposurePlan
from app.models_memory import UserPracticeSummary
from app.models_roleplay import RoleplaySession
from app.memory.profile_projection import build_user_practice_summary


class PostgresUserProfileRepository:
    """Build privacy-minimized practice summaries from PostgreSQL records."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or shared_postgres_engine(
            database_url or database_settings().database_url
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
        return build_user_practice_summary(
            sessions=sessions,
            worksheet_count=worksheet_count,
            exposure_plan=plan,
        )
