"""Integration tests for the PostgreSQL exposure repository."""

from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.postgres.exposure_repository import PostgresExposureRepository
from app.models_exposure import (
    EXPOSURE_DISCLAIMER,
    ExposureAttempt,
    ExposureFeedbackStatus,
    ExposurePlan,
    ExposureTask,
)


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests.",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply Alembic migrations to the configured test database."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    command.upgrade(config, "head")


@pytest.fixture
def repository() -> PostgresExposureRepository:
    """Return a PostgreSQL exposure repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresExposureRepository(database_url=TEST_DATABASE_URL)


@pytest.mark.anyio
async def test_postgres_exposure_save_get_and_owner_scope(
    repository: PostgresExposureRepository,
) -> None:
    plan = _exposure_plan(user_id=f"pg_exposure_user_{uuid4().hex}")

    saved = await repository.save_plan(plan)
    by_user = await repository.get_for_user(plan.user_id)
    by_id = await repository.get_by_id_for_user(plan.plan_id, plan.user_id)
    wrong_user = await repository.get_by_id_for_user(plan.plan_id, "other_user")

    assert saved.plan_id == plan.plan_id
    assert by_user is not None
    assert by_user.plan_id == plan.plan_id
    assert by_id is not None
    assert by_id.target_scenario == "[raw exposure target scenario minimized by privacy policy]"
    assert wrong_user is None

    async with repository.engine.connect() as connection:
        row = (await connection.execute(
            text(
                """SELECT current_anxiety_level, recommended_next_task_id, deleted_at
                FROM exposure_plans WHERE plan_id = :plan_id"""
            ),
            {"plan_id": plan.plan_id},
        )).mappings().first()

    assert row is not None
    assert row["current_anxiety_level"] == 6
    assert row["recommended_next_task_id"] == "task_1"
    assert row["deleted_at"] is None


@pytest.mark.anyio
async def test_postgres_exposure_save_plan_replaces_active_user_plan(
    repository: PostgresExposureRepository,
) -> None:
    user_id = f"pg_exposure_replace_user_{uuid4().hex}"
    first = _exposure_plan(user_id=user_id, target_scenario="first minimized scenario")
    second = _exposure_plan(user_id=user_id, target_scenario="second minimized scenario")

    await repository.save_plan(first)
    await repository.save_attempt(
        user_id,
        first.model_copy(update={"attempts": [_attempt("task_1")]}),
        _attempt("task_1"),
    )
    await repository.save_plan(second)

    active = await repository.get_for_user(user_id)
    old = await repository.get_by_id_for_user(first.plan_id, user_id)

    assert active is not None
    assert active.plan_id == second.plan_id
    assert active.target_scenario == "second minimized scenario"
    assert old is None


@pytest.mark.anyio
async def test_postgres_exposure_save_attempt_updates_plan_payload(
    repository: PostgresExposureRepository,
) -> None:
    plan = _exposure_plan(user_id=f"pg_exposure_attempt_user_{uuid4().hex}")
    attempt = _attempt("task_1")
    updated = plan.model_copy(
        update={
            "attempts": [attempt],
            "recommended_next_task_id": "task_2",
            "updated_at": datetime.now(timezone.utc),
        }
    )

    await repository.save_plan(plan)
    await repository.save_attempt(plan.user_id, updated, attempt)
    fetched = await repository.get_for_user(plan.user_id)

    assert fetched is not None
    assert len(fetched.attempts) == 1
    assert fetched.attempts[0].status == ExposureFeedbackStatus.COMPLETED
    assert fetched.attempts[0].reflection == "[minimized exposure reflection]"
    assert fetched.recommended_next_task_id == "task_2"

    async with repository.engine.connect() as connection:
        row = (await connection.execute(
            text(
                """SELECT task_id, status, anxiety_before, anxiety_after
                FROM exposure_attempts WHERE plan_id = :plan_id"""
            ),
            {"plan_id": plan.plan_id},
        )).mappings().first()

    assert row is not None
    assert row["task_id"] == "task_1"
    assert row["status"] == "completed"
    assert row["anxiety_before"] == 6
    assert row["anxiety_after"] == 4


def _exposure_plan(
    *,
    user_id: str,
    target_scenario: str = "[raw exposure target scenario minimized by privacy policy]",
) -> ExposurePlan:
    """Build a product-safe demo exposure plan."""
    now = datetime.now(timezone.utc)
    tasks = [
        ExposureTask(
            task_id="task_1",
            title="Demo first step",
            description="A small, stoppable social practice step.",
            difficulty=3,
            estimated_time_minutes=5,
            success_criteria="Complete one small step.",
            fallback_task="Pause and choose a smaller version.",
        ),
        ExposureTask(
            task_id="task_2",
            title="Demo second step",
            description="A slightly larger social practice step.",
            difficulty=5,
            estimated_time_minutes=8,
            success_criteria="Try the next step while keeping it optional.",
            fallback_task="Return to the first step.",
        ),
    ]
    return ExposurePlan(
        plan_id=f"plan_{uuid4().hex}",
        user_id=user_id,
        target_scenario=target_scenario,
        current_anxiety_level=6,
        previous_attempts=["[minimized previous attempt]"],
        tasks=tasks,
        attempts=[],
        recommended_next_task_id="task_1",
        disclaimer=EXPOSURE_DISCLAIMER,
        created_at=now,
        updated_at=now,
    )


def _attempt(task_id: str) -> ExposureAttempt:
    """Build a product-safe demo exposure attempt."""
    return ExposureAttempt(
        task_id=task_id,
        status=ExposureFeedbackStatus.COMPLETED,
        anxiety_before=6,
        anxiety_after=4,
        reflection="[minimized exposure reflection]",
        created_at=datetime.now(timezone.utc),
    )
