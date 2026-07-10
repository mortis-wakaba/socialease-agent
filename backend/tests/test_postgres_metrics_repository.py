"""Integration tests for the PostgreSQL metrics repository."""

from datetime import datetime, timezone
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.postgres.metrics_repository import PostgresMetricsRepository
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
def repository() -> PostgresMetricsRepository:
    """Return a PostgreSQL metrics repository for tests."""
    assert TEST_DATABASE_URL is not None
    repo = PostgresMetricsRepository(database_url=TEST_DATABASE_URL)
    repo.reset()
    return repo


def test_postgres_metrics_records_aggregate_safe_fields(
    repository: PostgresMetricsRepository,
) -> None:
    repository.record_trace(
        TraceRecord(
            run_id="run_should_not_be_persisted_in_metrics",
            user_id="user_should_not_be_persisted_in_metrics",
            input="user text should not be persisted in metrics",
            safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="demo"),
            intent_result=IntentResult(
                intent=Intent.ROLEPLAY_PRACTICE,
                confidence=0.9,
                reason="demo",
            ),
            selected_skill="roleplay_skill",
            selected_agent="roleplay_agent",
            action="start_roleplay",
            permission_action="ask_consent",
            permission_reason="demo",
            output="assistant text should not be persisted in metrics",
            latency_ms=10.0,
            errors=[],
            created_at=datetime.now(timezone.utc),
        )
    )
    repository.record_trace(
        TraceRecord(
            run_id="crisis_run_should_not_be_persisted_in_metrics",
            user_id="crisis_user_should_not_be_persisted_in_metrics",
            input="crisis text should not be persisted in metrics",
            safety_result=SafetyResult(risk_level=RiskLevel.CRISIS, reason="demo"),
            intent_result=IntentResult(intent=Intent.CRISIS, confidence=1.0, reason="demo"),
            selected_skill="crisis_escalation_skill",
            selected_agent="crisis_escalation",
            action="crisis_escalation",
            permission_action="escalate",
            output="crisis response",
            latency_ms=30.0,
            errors=["before_memory_write_blocked:demo"],
            created_at=datetime.now(timezone.utc),
        )
    )
    repository.record_runtime_event("rate_limit_hit")
    repository.record_runtime_event("llm_concurrency_saturation", count=2)

    snapshot = repository.snapshot()

    assert snapshot.total_runs == 2
    assert snapshot.crisis_runs == 1
    assert snapshot.average_latency_ms == 20.0
    assert snapshot.permission_counts["ask_consent"] == 1
    assert snapshot.permission_counts["escalate"] == 1
    assert snapshot.product_boundary_eval_counts["permission_ask_consent"] == 1
    assert snapshot.product_boundary_eval_counts["crisis_escalated"] == 1
    assert snapshot.rate_limit_hits == 1
    assert snapshot.llm_concurrency_saturation == 2

    with repository.engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM harness_metric_events LIMIT 1")
        ).mappings().first()
        runtime_row = connection.execute(
            text("SELECT * FROM harness_runtime_metric_events LIMIT 1")
        ).mappings().first()

    assert row is not None
    assert "run_id" not in row
    assert "user_id" not in row
    assert "input" not in row
    assert "output" not in row
    assert runtime_row is not None
    assert "user_id" not in runtime_row
    assert "input" not in runtime_row
    assert "output" not in runtime_row


def test_postgres_metrics_reset_clears_events(
    repository: PostgresMetricsRepository,
) -> None:
    repository.record_trace(
        TraceRecord(
            run_id="reset_demo_run",
            user_id="reset_demo_user",
            input="not persisted",
            safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="demo"),
            intent_result=IntentResult(
                intent=Intent.EMOTIONAL_SUPPORT,
                confidence=0.9,
                reason="demo",
            ),
            selected_agent="support_agent",
            output="not persisted",
            latency_ms=5.0,
            created_at=datetime.now(timezone.utc),
        )
    )

    repository.reset()
    snapshot = repository.snapshot()

    assert snapshot.total_runs == 0
