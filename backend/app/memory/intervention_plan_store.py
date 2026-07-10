"""Intervention plan store backed by SQLite."""

from datetime import datetime, timezone
from uuid import uuid4

from app.db.config import database_settings
from app.db.engine import connect
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.db.session import initialize_database
from app.models_intervention import InterventionPlan, InterventionStep


class InterventionPlanStore:
    """Persist session-level intervention plans."""

    def __init__(self) -> None:
        if resolve_database_provider(database_settings().database_url) == DatabaseProvider.SQLITE:
            initialize_database()

    def create(
        self,
        *,
        user_id: str,
        session_id: str,
        steps: list[InterventionStep],
        status: str = "active",
        protocol_id: str | None = None,
    ) -> InterventionPlan:
        """Create and persist an active intervention plan."""
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
        with connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO intervention_plans
                (plan_id, user_id, session_id, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan.plan_id,
                    plan.user_id,
                    plan.session_id,
                    plan.status,
                    plan.model_dump_json(),
                    plan.created_at.isoformat(),
                    plan.updated_at.isoformat(),
                ),
            )
        return plan

    def get_for_session(self, session_id: str, user_id: str) -> InterventionPlan | None:
        """Return the intervention plan for a user session."""
        with connect() as connection:
            row = connection.execute(
                "SELECT payload FROM intervention_plans WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        return InterventionPlan.model_validate_json(row["payload"]) if row else None

    def get_by_id_for_user(self, plan_id: str, user_id: str) -> InterventionPlan | None:
        """Return an intervention plan by id only if it belongs to the user."""
        with connect() as connection:
            row = connection.execute(
                "SELECT payload FROM intervention_plans WHERE plan_id = ? AND user_id = ?",
                (plan_id, user_id),
            ).fetchone()
        return InterventionPlan.model_validate_json(row["payload"]) if row else None

    def list_for_user(self, user_id: str, limit: int = 20) -> list[InterventionPlan]:
        """Return recent intervention plans for one user."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM intervention_plans
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [InterventionPlan.model_validate_json(row["payload"]) for row in rows]

    def cancel_pending_consent_before(self, cutoff: datetime) -> int:
        """Cancel abandoned pending-consent plans before a cutoff timestamp."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM intervention_plans
                WHERE status = ? AND updated_at <= ?""",
                ("pending_consent", cutoff.isoformat()),
            ).fetchall()
        cancelled_count = 0
        for row in rows:
            plan = InterventionPlan.model_validate_json(row["payload"])
            updated_steps = [
                step.model_copy(update={"status": "cancelled"})
                if step.status in {"pending", "in_progress"}
                else step
                for step in plan.steps
            ]
            updated = plan.model_copy(update={"status": "cancelled", "steps": updated_steps})
            self.save(updated)
            cancelled_count += 1
        return cancelled_count


intervention_plan_store = InterventionPlanStore()
