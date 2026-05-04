"""In-memory exposure plan store with a replaceable repository shape."""

from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from app.models_exposure import (
    EXPOSURE_DISCLAIMER,
    ExposureAttempt,
    ExposurePlan,
    ExposureTask,
)


class ExposureStore:
    """Store one active exposure plan per user for the MVP."""

    def __init__(self) -> None:
        self._plans_by_user: dict[str, ExposurePlan] = {}
        self._lock = Lock()

    def create_plan(
        self,
        user_id: str,
        target_scenario: str,
        current_anxiety_level: int,
        previous_attempts: list[str],
        tasks: list[ExposureTask],
    ) -> ExposurePlan:
        """Create or replace the user's active exposure plan."""
        now = datetime.now(timezone.utc)
        plan = ExposurePlan(
            plan_id=str(uuid4()),
            user_id=user_id,
            target_scenario=target_scenario,
            current_anxiety_level=current_anxiety_level,
            previous_attempts=previous_attempts,
            tasks=tasks,
            attempts=[],
            recommended_next_task_id=tasks[0].task_id if tasks else None,
            disclaimer=EXPOSURE_DISCLAIMER,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._plans_by_user[user_id] = plan
        return plan

    def get_for_user(self, user_id: str) -> ExposurePlan | None:
        """Return the user's active exposure plan, if present."""
        with self._lock:
            return self._plans_by_user.get(user_id)

    def update_after_attempt(
        self,
        user_id: str,
        attempt: ExposureAttempt,
        recommended_next_task_id: str | None,
    ) -> ExposurePlan | None:
        """Append an attempt and update the recommended next task."""
        with self._lock:
            plan = self._plans_by_user.get(user_id)
            if plan is None:
                return None
            updated = plan.model_copy(
                update={
                    "attempts": [*plan.attempts, attempt],
                    "recommended_next_task_id": recommended_next_task_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._plans_by_user[user_id] = updated
            return updated


exposure_store = ExposureStore()

