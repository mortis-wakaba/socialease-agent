"""Integration tests for the PostgreSQL worksheet repository."""

from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.db.postgres.worksheet_repository import PostgresWorksheetRepository
from app.models_worksheet import WORKSHEET_DISCLAIMER, WorksheetFields, WorksheetRecord


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
def repository() -> PostgresWorksheetRepository:
    """Return a PostgreSQL worksheet repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresWorksheetRepository(database_url=TEST_DATABASE_URL)


def test_postgres_worksheet_save_and_get(repository: PostgresWorksheetRepository) -> None:
    record = _worksheet_record(user_id=f"pg_worksheet_user_{uuid4().hex}")

    saved = repository.save(record)
    fetched = repository.get(record.worksheet_id)

    assert saved.worksheet_id == record.worksheet_id
    assert fetched is not None
    assert fetched.worksheet_id == record.worksheet_id
    assert fetched.user_id == record.user_id
    assert fetched.source_message == "[minimized demo source]"
    assert fetched.fields.emotion == "紧张"
    assert fetched.disclaimer == WORKSHEET_DISCLAIMER


def test_postgres_worksheet_save_upserts_existing_record(
    repository: PostgresWorksheetRepository,
) -> None:
    record = _worksheet_record(user_id=f"pg_worksheet_upsert_user_{uuid4().hex}")
    updated = record.model_copy(
        update={
            "fields": record.fields.model_copy(update={"emotion": "焦虑"}),
            "missing_fields": ["evidence_against"],
        }
    )

    repository.save(record)
    repository.save(updated)
    fetched = repository.get(record.worksheet_id)

    assert fetched is not None
    assert fetched.fields.emotion == "焦虑"
    assert fetched.missing_fields == ["evidence_against"]


def _worksheet_record(*, user_id: str) -> WorksheetRecord:
    """Build a product-safe demo worksheet record."""
    return WorksheetRecord(
        worksheet_id=f"worksheet_{uuid4().hex}",
        user_id=user_id,
        source_message="[minimized demo source]",
        fields=WorksheetFields(
            situation="课堂发言",
            automatic_thought="我可能会说错",
            emotion="紧张",
            emotion_intensity=6,
            evidence_for="之前发言时卡过壳",
            evidence_against="也有表达清楚的时候",
            alternative_thought="我可以先说一个核心观点",
            next_action="练习开场两遍",
        ),
        citations=[],
        disclaimer=WORKSHEET_DISCLAIMER,
        missing_fields=[],
        gentle_followup_questions=[],
        created_at=datetime.now(timezone.utc),
    )
