"""PostgreSQL exposure-plan repository implementation."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models_exposure import ExposureAttempt, ExposurePlan


class PostgresExposureRepository:
    """PostgreSQL-backed active exposure plan repository."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def save_plan(self, plan: ExposurePlan) -> ExposurePlan:
        """Create or replace one user's active exposure plan."""
        with self.engine.begin() as connection:
            existing = connection.execute(
                text("SELECT plan_id FROM exposure_plans WHERE user_id = :user_id"),
                {"user_id": plan.user_id},
            ).mappings().first()
            if existing:
                connection.execute(
                    text("DELETE FROM exposure_attempts WHERE plan_id = :plan_id"),
                    {"plan_id": existing["plan_id"]},
                )
                connection.execute(
                    text("DELETE FROM exposure_plans WHERE plan_id = :plan_id"),
                    {"plan_id": existing["plan_id"]},
                )
            connection.execute(
                text(
                    """INSERT INTO exposure_plans
                    (plan_id, user_id, current_anxiety_level, recommended_next_task_id,
                    payload, created_at, updated_at)
                    VALUES (:plan_id, :user_id, :current_anxiety_level,
                    :recommended_next_task_id, CAST(:payload AS jsonb), :created_at, :updated_at)
                    ON CONFLICT (plan_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        current_anxiety_level = EXCLUDED.current_anxiety_level,
                        recommended_next_task_id = EXCLUDED.recommended_next_task_id,
                        payload = EXCLUDED.payload,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at"""
                ),
                _plan_params(plan),
            )
        return plan

    def get_for_user(self, user_id: str) -> ExposurePlan | None:
        """Return a user's active exposure plan, if present."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT payload FROM exposure_plans WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).mappings().first()
        return ExposurePlan.model_validate(row["payload"]) if row else None

    def get_by_id_for_user(self, plan_id: str, user_id: str) -> ExposurePlan | None:
        """Return a plan by id only if it belongs to the user."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM exposure_plans
                    WHERE plan_id = :plan_id AND user_id = :user_id"""
                ),
                {"plan_id": plan_id, "user_id": user_id},
            ).mappings().first()
        return ExposurePlan.model_validate(row["payload"]) if row else None

    def save_attempt(
        self,
        user_id: str,
        plan: ExposurePlan,
        attempt: ExposureAttempt,
    ) -> ExposurePlan:
        """Append one attempt row and persist the updated plan payload."""
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO exposure_attempts
                    (plan_id, user_id, task_id, status, anxiety_before, anxiety_after,
                    payload, created_at)
                    VALUES (:plan_id, :user_id, :task_id, :status, :anxiety_before,
                    :anxiety_after, CAST(:payload AS jsonb), :created_at)"""
                ),
                {
                    "plan_id": plan.plan_id,
                    "user_id": user_id,
                    "task_id": attempt.task_id,
                    "status": attempt.status.value,
                    "anxiety_before": attempt.anxiety_before,
                    "anxiety_after": attempt.anxiety_after,
                    "payload": attempt.model_dump_json(),
                    "created_at": attempt.created_at,
                },
            )
            connection.execute(
                text(
                    """UPDATE exposure_plans
                    SET current_anxiety_level = :current_anxiety_level,
                        recommended_next_task_id = :recommended_next_task_id,
                        payload = CAST(:payload AS jsonb),
                        updated_at = :updated_at
                    WHERE plan_id = :plan_id AND user_id = :user_id"""
                ),
                {
                    "current_anxiety_level": plan.current_anxiety_level,
                    "recommended_next_task_id": plan.recommended_next_task_id,
                    "payload": plan.model_dump_json(),
                    "updated_at": plan.updated_at,
                    "plan_id": plan.plan_id,
                    "user_id": user_id,
                },
            )
        return plan


def _plan_params(plan: ExposurePlan) -> dict[str, object]:
    """Return SQL parameters for an exposure plan."""
    return {
        "plan_id": plan.plan_id,
        "user_id": plan.user_id,
        "current_anxiety_level": plan.current_anxiety_level,
        "recommended_next_task_id": plan.recommended_next_task_id,
        "payload": plan.model_dump_json(),
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }
