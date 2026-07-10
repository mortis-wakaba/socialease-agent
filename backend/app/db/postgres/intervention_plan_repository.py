"""PostgreSQL intervention plan repository implementation."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models_intervention import InterventionPlan, InterventionStep


class PostgresInterventionPlanRepository:
    """PostgreSQL-backed intervention plan repository."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def create(
        self,
        *,
        user_id: str,
        session_id: str,
        steps: list[InterventionStep],
        status: str = "active",
        protocol_id: str | None = None,
    ) -> InterventionPlan:
        """Create and persist an intervention plan."""
        now = datetime.now(timezone.utc)
        plan = InterventionPlan(
            plan_id=str(uuid4()),
            user_id=user_id,
            session_id=session_id,
            status=status,  # type: ignore[arg-type]
            protocol_id=protocol_id,
            steps=steps,
            created_at=now,
            updated_at=now,
        )
        return self.save(plan)

    def save(self, plan: InterventionPlan) -> InterventionPlan:
        """Persist one intervention plan."""
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO intervention_plans
                    (plan_id, user_id, session_id, status, payload, created_at, updated_at)
                    VALUES
                    (:plan_id, :user_id, :session_id, :status, CAST(:payload AS jsonb),
                    :created_at, :updated_at)
                    ON CONFLICT (plan_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        session_id = EXCLUDED.session_id,
                        status = EXCLUDED.status,
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at"""
                ),
                _plan_params(plan),
            )
        return plan

    def get_for_session(self, session_id: str, user_id: str) -> InterventionPlan | None:
        """Return the intervention plan for a user session."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM intervention_plans
                    WHERE session_id = :session_id AND user_id = :user_id"""
                ),
                {"session_id": session_id, "user_id": user_id},
            ).mappings().first()
        return InterventionPlan.model_validate(row["payload"]) if row else None

    def get_by_id_for_user(self, plan_id: str, user_id: str) -> InterventionPlan | None:
        """Return an intervention plan by id only if it belongs to the user."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM intervention_plans
                    WHERE plan_id = :plan_id AND user_id = :user_id"""
                ),
                {"plan_id": plan_id, "user_id": user_id},
            ).mappings().first()
        return InterventionPlan.model_validate(row["payload"]) if row else None

    def list_for_user(self, user_id: str, limit: int = 20) -> list[InterventionPlan]:
        """Return recent intervention plans for one user."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM intervention_plans
                    WHERE user_id = :user_id
                    ORDER BY updated_at DESC
                    LIMIT :limit"""
                ),
                {"user_id": user_id, "limit": limit},
            ).mappings().all()
        return [InterventionPlan.model_validate(row["payload"]) for row in rows]

    def cancel_pending_consent_before(self, cutoff: datetime) -> int:
        """Cancel abandoned pending-consent plans before a cutoff timestamp."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM intervention_plans
                    WHERE status = :status AND updated_at <= :cutoff"""
                ),
                {"status": "pending_consent", "cutoff": cutoff},
            ).mappings().all()
        cancelled_count = 0
        for row in rows:
            plan = InterventionPlan.model_validate(row["payload"])
            updated_steps = [
                step.model_copy(update={"status": "cancelled"})
                if step.status in {"pending", "in_progress"}
                else step
                for step in plan.steps
            ]
            updated = plan.model_copy(
                update={
                    "status": "cancelled",
                    "steps": updated_steps,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self.save(updated)
            cancelled_count += 1
        return cancelled_count


def _plan_params(plan: InterventionPlan) -> dict[str, object]:
    """Return SQL parameters for an intervention plan."""
    return {
        "plan_id": plan.plan_id,
        "user_id": plan.user_id,
        "session_id": plan.session_id,
        "status": plan.status,
        "payload": plan.model_dump_json(),
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }
