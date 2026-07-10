"""Integration tests for the PostgreSQL trace repository."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.postgres.trace_repository import PostgresTraceRepository
from app.models import Intent, IntentResult, RiskLevel, SafetyResult, TraceRecord


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
def repository() -> PostgresTraceRepository:
    """Return a PostgreSQL trace repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresTraceRepository(database_url=TEST_DATABASE_URL)


def test_postgres_trace_save_and_get(repository: PostgresTraceRepository) -> None:
    record = _trace_record(user_id=f"pg_trace_user_{uuid4().hex}")

    saved = repository.save(record)
    fetched = repository.get(record.run_id)

    assert saved.run_id == record.run_id
    assert fetched is not None
    assert fetched.run_id == record.run_id
    assert fetched.user_id == record.user_id
    assert fetched.product_safe is True
    assert fetched.safety_result.risk_level == RiskLevel.LOW
    assert fetched.intent_result.intent == Intent.EMOTIONAL_SUPPORT

    with repository.engine.connect() as connection:
        row = connection.execute(
            text(
                """SELECT risk_level, intent, selected_agent, permission_action,
                session_id, intervention_plan_id
                FROM runs WHERE run_id = :run_id"""
            ),
            {"run_id": record.run_id},
        ).mappings().first()

    assert row is not None
    assert row["risk_level"] == "low"
    assert row["intent"] == "emotional_support"
    assert row["selected_agent"] == "support_agent"
    assert row["permission_action"] is None
    assert row["session_id"] is None
    assert row["intervention_plan_id"] is None


def test_postgres_trace_list_recent_orders_newest_first(
    repository: PostgresTraceRepository,
) -> None:
    user_id = f"pg_trace_recent_user_{uuid4().hex}"
    future_base = datetime.now(timezone.utc) + timedelta(days=3650)
    older = _trace_record(
        user_id=user_id,
        created_at=future_base,
    )
    newer = _trace_record(user_id=user_id, created_at=future_base + timedelta(seconds=1))

    repository.save(older)
    repository.save(newer)
    recent = repository.list_recent(limit=2)

    assert recent[0].run_id == newer.run_id
    assert {record.run_id for record in recent} >= {older.run_id, newer.run_id}


def _trace_record(
    *,
    user_id: str,
    created_at: datetime | None = None,
) -> TraceRecord:
    """Build a product-safe demo trace record."""
    return TraceRecord(
        run_id=f"run_{uuid4().hex}",
        user_id=user_id,
        input="[redacted demo input]",
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="demo low risk"),
        intent_result=IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=0.7,
            reason="demo keyword route",
        ),
        selected_agent="support_agent",
        output="[redacted demo output]",
        latency_ms=12.5,
        created_at=created_at or datetime.now(timezone.utc),
    )
