"""Exposure plan store backed by a replaceable repository."""

from datetime import datetime, timezone
from uuid import uuid4

from app.db.repositories import ExposureRepository, SQLiteExposureRepository
from app.models_exposure import (
    EXPOSURE_DISCLAIMER,
    ExposureAttempt,
    ExposurePlan,
    ExposureTask,
)


class ExposureStore:
    """Coordinate active exposure plans and repository persistence."""

    def __init__(self, repository: ExposureRepository | None = None) -> None:
        self.repository = repository or SQLiteExposureRepository()

    async def create_plan(
        self,
        user_id: str,
        target_scenario: str,
        current_anxiety_level: int,
        previous_attempts: list[str],
        tasks: list[ExposureTask],
        *,
        plan_id: str | None = None,
    ) -> ExposurePlan:
        """Create or replace the user's active exposure plan."""
        now = datetime.now(timezone.utc)
        plan = ExposurePlan(
            plan_id=plan_id or str(uuid4()),
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
        return await self.repository.save_plan(plan)

    async def get_for_user(self, user_id: str) -> ExposurePlan | None:
        """Return the user's active exposure plan, if present."""
        return await self.repository.get_for_user(user_id)

    async def get_by_id_for_user(self, plan_id: str, user_id: str) -> ExposurePlan | None:
        """Return a plan by id only if it belongs to the user."""
        return await self.repository.get_by_id_for_user(
            plan_id=plan_id, user_id=user_id
        )

    async def update_after_attempt(
        self,
        user_id: str,
        attempt: ExposureAttempt,
        recommended_next_task_id: str | None,
    ) -> ExposurePlan | None:
        """Append an attempt and update the recommended next task."""
        plan = await self.repository.get_for_user(user_id)
        if plan is None:
            return None
        updated = plan.model_copy(
            update={
                "attempts": [*plan.attempts, attempt],
                "recommended_next_task_id": recommended_next_task_id,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return await self.repository.save_attempt(user_id, updated, attempt)


exposure_store = ExposureStore()
