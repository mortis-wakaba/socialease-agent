"""Tests for the aggregate metrics backend."""

from pathlib import Path
from datetime import datetime, timezone

import pytest

from app.models import Intent, IntentResult, RiskLevel, SafetyResult, TraceRecord
from app.observability.metrics import SQLiteMetricsRepository


@pytest.mark.anyio
async def test_sqlite_metrics_repository_records_non_identifying_aggregates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "metrics.db"))
    repository = SQLiteMetricsRepository()
    await repository.reset()

    await repository.record_trace(
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
    await repository.record_trace(
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
    await repository.record_runtime_event("rate_limit_hit")
    await repository.record_runtime_event("llm_concurrency_saturation", count=2)
    await repository.record_runtime_event("slow_request")
    await repository.record_runtime_event("memory_export", count=2)
    await repository.record_runtime_event("memory_delete")
    await repository.record_runtime_event("memory_preferences_saved")
    await repository.record_runtime_event("memory_preferences_disabled")

    snapshot = await repository.snapshot()

    assert snapshot.total_runs == 2
    assert snapshot.crisis_runs == 1
    assert snapshot.average_latency_ms == 20.0
    assert snapshot.latency_p50_ms == 10.0
    assert snapshot.latency_p95_ms == 30.0
    assert snapshot.permission_counts["ask_consent"] == 1
    assert snapshot.permission_counts["escalate"] == 1
    assert snapshot.product_boundary_eval_counts["permission_ask_consent"] == 1
    assert snapshot.product_boundary_eval_counts["crisis_escalated"] == 1
    assert snapshot.rate_limit_hits == 1
    assert snapshot.llm_concurrency_saturation == 2
    assert snapshot.slow_request_count == 1
    assert snapshot.memory_export_count == 2
    assert snapshot.memory_delete_count == 1
    assert snapshot.memory_preferences_saved_count == 1
    assert snapshot.memory_preferences_disabled_count == 1
    assert snapshot.runtime_event_counts["rate_limit_hit"] == 1
