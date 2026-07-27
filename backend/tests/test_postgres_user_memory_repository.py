"""Integration tests for PostgreSQL user memory repositories."""

from datetime import datetime, timezone
import json
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.postgres.memory_settings_repository import PostgresUserMemorySettingsRepository
from app.db.postgres.user_profile_repository import PostgresUserProfileRepository
from app.models_exposure import ExposureAttempt, ExposureFeedbackStatus, ExposurePlan, ExposureTask
from app.models_memory import PracticePreferences, UserConsentState
from app.models_roleplay import RoleplayGuidance, RoleplaySession
from app.services.memory_privacy_service import MemoryPrivacyService


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
def settings_repository() -> PostgresUserMemorySettingsRepository:
    """Return a PostgreSQL memory settings repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresUserMemorySettingsRepository(database_url=TEST_DATABASE_URL)


@pytest.fixture
def profile_repository() -> PostgresUserProfileRepository:
    """Return a PostgreSQL user-profile repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresUserProfileRepository(database_url=TEST_DATABASE_URL)


def test_postgres_memory_settings_save_and_get(
    settings_repository: PostgresUserMemorySettingsRepository,
) -> None:
    user_id = f"pg_memory_settings_user_{uuid4().hex}"
    settings = settings_repository.save(
        user_id=user_id,
        consent_state=UserConsentState(
            consent_to_practice_summary=True,
            consent_to_save_preferences=True,
            do_not_store_raw_messages=True,
            allow_sensitive_memory=False,
        ),
        practice_preferences=PracticePreferences(
            preferred_roleplay_difficulty=4,
            preferred_feedback_style="brief_actionable",
            preferred_practice_scenarios=["classroom_speech"],
        ),
    )
    fetched = settings_repository.get(user_id)

    assert settings.consent_state.consent_to_save_preferences is True
    assert fetched.practice_preferences.preferred_roleplay_difficulty == 4
    assert fetched.practice_preferences.preferred_practice_scenarios == ["classroom_speech"]
    assert fetched.consent_state.allow_sensitive_memory is False


def test_postgres_memory_settings_schema_evolution_payload_is_sanitized(
    settings_repository: PostgresUserMemorySettingsRepository,
    profile_repository: PostgresUserProfileRepository,
) -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_memory_schema_guard_{uuid4().hex}"
    raw_sensitive_values = [
        "手机号 13912345678",
        "pref_schema_pg@example.com",
        "北京市海淀区中关村大街27号",
        "姓名：张三",
    ]
    historical_payload = {
        "consent_state": {
            "consent_to_practice_summary": True,
            "consent_to_save_preferences": True,
            "do_not_store_raw_messages": True,
            "allow_sensitive_memory": False,
        },
        "practice_preferences": {
            "preferred_roleplay_difficulty": 4,
            "preferred_feedback_style": "鼓励反思型",
            "preferred_practice_scenarios": [
                "classroom_speech",
                raw_sensitive_values[2],
            ],
        },
        "onboarding_profile": {
            "primary_goal": raw_sensitive_values[3],
            "preferred_scenario": "classroom_speech",
            "current_anxiety_level": 7,
            "practice_preference": raw_sensitive_values[1],
            "boundary_acknowledged": True,
        },
        "unexpected_free_text": raw_sensitive_values[0],
    }
    with settings_repository.engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO user_memory_settings (user_id, payload, updated_at)
                VALUES (:user_id, CAST(:payload AS jsonb), :updated_at)
                ON CONFLICT (user_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at"""
            ),
            {
                "user_id": user_id,
                "payload": json.dumps(historical_payload, ensure_ascii=False),
                "updated_at": datetime.now(timezone.utc),
            },
        )

    fetched = settings_repository.get(user_id)
    service = MemoryPrivacyService(
        profile_repository=profile_repository,
        settings_repository=settings_repository,
        database_url=TEST_DATABASE_URL,
    )
    exported = service.export(user_id)

    assert fetched.practice_preferences.preferred_roleplay_difficulty == 4
    assert fetched.practice_preferences.preferred_feedback_style == (
        "encouraging_reflective"
    )
    assert fetched.practice_preferences.preferred_practice_scenarios == [
        "classroom_speech"
    ]
    serialized_export = str(exported.model_dump(mode="json"))
    for raw in raw_sensitive_values:
        assert raw not in serialized_export


def test_postgres_user_profile_summary_from_practice_records(
    profile_repository: PostgresUserProfileRepository,
) -> None:
    user_id = f"pg_profile_user_{uuid4().hex}"
    now = datetime.now(timezone.utc)
    roleplay = RoleplaySession(
        session_id=f"session_{uuid4().hex}",
        user_id=user_id,
        scenario="classroom_speech",
        difficulty=4,
        messages=[],
        retrieved_guidance=RoleplayGuidance(
            query="classroom_speech",
            answer="demo guidance",
            citations=[],
            unknown=False,
            confidence=0.8,
        ),
        created_at=now,
        updated_at=now,
    )
    plan = ExposurePlan(
        plan_id=f"plan_{uuid4().hex}",
        user_id=user_id,
        target_scenario="课堂发言",
        current_anxiety_level=7,
        previous_attempts=[],
        tasks=[
            ExposureTask(
                task_id="task_1",
                title="Demo practice",
                description="A small demo social practice task.",
                difficulty=3,
                estimated_time_minutes=5,
                success_criteria="Complete one small step.",
                fallback_task="Pause and try a smaller step.",
            )
        ],
        attempts=[
            ExposureAttempt(
                task_id="task_1",
                status=ExposureFeedbackStatus.COMPLETED,
                anxiety_before=7,
                anxiety_after=4,
                reflection="[minimized reflection]",
                created_at=now,
            )
        ],
        recommended_next_task_id="task_1",
        created_at=now,
        updated_at=now,
    )

    with profile_repository.engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO roleplay_sessions
                (session_id, user_id, payload, created_at, updated_at)
                VALUES (:session_id, :user_id, CAST(:payload AS jsonb), :created_at, :updated_at)"""
            ),
            {
                "session_id": roleplay.session_id,
                "user_id": user_id,
                "payload": roleplay.model_dump_json(),
                "created_at": roleplay.created_at,
                "updated_at": roleplay.updated_at,
            },
        )
        connection.execute(
            text(
                """INSERT INTO worksheets
                (worksheet_id, user_id, payload, created_at)
                VALUES (:worksheet_id, :user_id, CAST(:payload AS jsonb), :created_at)"""
            ),
            {
                "worksheet_id": f"worksheet_{uuid4().hex}",
                "user_id": user_id,
                "payload": '{"demo": true}',
                "created_at": now,
            },
        )
        connection.execute(
            text(
                """INSERT INTO exposure_plans
                (plan_id, user_id, payload, created_at, updated_at)
                VALUES (:plan_id, :user_id, CAST(:payload AS jsonb), :created_at, :updated_at)"""
            ),
            {
                "plan_id": plan.plan_id,
                "user_id": user_id,
                "payload": plan.model_dump_json(),
                "created_at": plan.created_at,
                "updated_at": plan.updated_at,
            },
        )

    summary = profile_repository.get_summary(user_id)

    assert summary.roleplay_session_count == 1
    assert summary.worksheet_count == 1
    assert summary.exposure_attempt_count == 1
    assert summary.latest_anxiety_level == 4
    assert summary.preferred_difficulty == 4
    assert summary.recent_scenarios == ["课堂发言", "classroom_speech"]


def test_postgres_memory_export_and_delete_cover_user_owned_records(
    settings_repository: PostgresUserMemorySettingsRepository,
    profile_repository: PostgresUserProfileRepository,
) -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_memory_export_delete_user_{uuid4().hex}"
    now = datetime.now(timezone.utc)
    roleplay = RoleplaySession(
        session_id=f"session_{uuid4().hex}",
        user_id=user_id,
        scenario="classroom_speech",
        difficulty=4,
        messages=[],
        retrieved_guidance=RoleplayGuidance(
            query="classroom_speech",
            answer="demo guidance",
            citations=[],
            unknown=False,
            confidence=0.8,
        ),
        created_at=now,
        updated_at=now,
    )
    settings_repository.save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_save_preferences=True),
        practice_preferences=PracticePreferences(
            preferred_roleplay_difficulty=4,
            preferred_feedback_style="brief_actionable",
            preferred_practice_scenarios=["classroom_speech"],
        ),
    )
    with profile_repository.engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO roleplay_sessions
                (session_id, user_id, scenario, difficulty, payload, created_at, updated_at)
                VALUES (
                    :session_id, :user_id, :scenario, :difficulty,
                    CAST(:payload AS jsonb), :created_at, :updated_at
                )"""
            ),
            {
                "session_id": roleplay.session_id,
                "user_id": user_id,
                "scenario": roleplay.scenario,
                "difficulty": roleplay.difficulty,
                "payload": roleplay.model_dump_json(),
                "created_at": roleplay.created_at,
                "updated_at": roleplay.updated_at,
            },
        )

    service = MemoryPrivacyService(
        profile_repository=profile_repository,
        settings_repository=settings_repository,
        database_url=TEST_DATABASE_URL,
    )

    exported = service.export(user_id)
    deleted = service.delete(user_id)
    exported_after_delete = service.export(user_id)

    assert len(exported.records["roleplay_sessions"]) == 1
    assert len(exported.records["user_memory_settings"]) == 1
    assert deleted.deleted_counts["roleplay_sessions"] >= 1
    assert deleted.deleted_counts["user_memory_settings"] >= 1
    assert deleted.profile_after_delete.practice_summary.roleplay_session_count == 0
    assert all(len(rows) == 0 for rows in exported_after_delete.records.values())
