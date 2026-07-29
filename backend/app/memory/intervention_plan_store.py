"""Repository contract for session-level intervention plans."""

from datetime import datetime
from typing import Protocol
from uuid import uuid4

from datetime import timezone

from app.models_intervention import InterventionPlan, InterventionStep


class InterventionPlanRepository(Protocol):
    """Persistence contract for session-level intervention plans."""

    async def create(
        self,
        *,
        user_id: str,
        session_id: str,
        steps: list[InterventionStep],
        status: str = "active",
        protocol_id: str | None = None,
    ) -> InterventionPlan: ...

    async def save(self, plan: InterventionPlan) -> InterventionPlan: ...

    async def get_for_session(
        self,
        session_id: str,
        user_id: str,
    ) -> InterventionPlan | None: ...

    async def get_by_id_for_user(
        self,
        plan_id: str,
        user_id: str,
    ) -> InterventionPlan | None: ...

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[InterventionPlan]: ...

    async def cancel_pending_consent_before(self, cutoff: datetime) -> int: ...


class InMemoryInterventionPlanRepository:
    """Non-persistent intervention-plan fake for unit tests and evals."""

    def __init__(self) -> None:
        self._plans: dict[str, InterventionPlan] = {}

    async def create(
        self,
        *,
        user_id: str,
        session_id: str,
        steps: list[InterventionStep],
        status: str = "active",
        protocol_id: str | None = None,
    ) -> InterventionPlan:
        """Create one in-memory plan."""
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
        self._plans[plan.plan_id] = plan
        return plan

    async def save(self, plan: InterventionPlan) -> InterventionPlan:
        """Save one in-memory plan."""
        self._plans[plan.plan_id] = plan
        return plan

    async def get_for_session(
        self,
        session_id: str,
        user_id: str,
    ) -> InterventionPlan | None:
        """Return a user-owned plan by session."""
        return next(
            (
                plan
                for plan in self._plans.values()
                if plan.session_id == session_id and plan.user_id == user_id
            ),
            None,
        )

    async def get_by_id_for_user(
        self,
        plan_id: str,
        user_id: str,
    ) -> InterventionPlan | None:
        """Return a plan only for its owner."""
        plan = self._plans.get(plan_id)
        return plan if plan is not None and plan.user_id == user_id else None

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[InterventionPlan]:
        """Return recent user-owned plans."""
        plans = [plan for plan in self._plans.values() if plan.user_id == user_id]
        return sorted(plans, key=lambda plan: plan.updated_at, reverse=True)[:limit]

    async def cancel_pending_consent_before(self, cutoff: datetime) -> int:
        """Cancel pending-consent plans at or before the cutoff."""
        cancelled = 0
        for plan_id, plan in tuple(self._plans.items()):
            if plan.status != "pending_consent" or plan.updated_at > cutoff:
                continue
            steps = [
                step.model_copy(update={"status": "cancelled"})
                if step.status in {"pending", "in_progress"}
                else step
                for step in plan.steps
            ]
            self._plans[plan_id] = plan.model_copy(
                update={
                    "status": "cancelled",
                    "steps": steps,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            cancelled += 1
        return cancelled
