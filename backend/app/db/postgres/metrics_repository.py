"""PostgreSQL aggregate metrics repository implementation."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models import RiskLevel, TraceRecord
from app.observability.metrics import (
    HarnessMetricsSnapshot,
    MetricsRepository,
    _product_boundary_eval_label,
    _record_row_into_snapshot,
)


class PostgresMetricsRepository(MetricsRepository):
    """PostgreSQL-backed metrics backend storing only aggregate-safe fields."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def record_trace(self, trace: TraceRecord) -> None:
        """Persist one non-identifying metrics event."""
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO harness_metric_events
                    (intent, risk_level, selected_agent, permission_action, latency_ms,
                    is_crisis, fallback_used, hook_blocked, memory_write_blocked,
                    product_boundary_eval, created_at)
                    VALUES
                    (:intent, :risk_level, :selected_agent, :permission_action, :latency_ms,
                    :is_crisis, :fallback_used, :hook_blocked, :memory_write_blocked,
                    :product_boundary_eval, :created_at)"""
                ),
                {
                    "intent": trace.intent_result.intent.value,
                    "risk_level": trace.safety_result.risk_level.value,
                    "selected_agent": trace.selected_agent,
                    "permission_action": trace.permission_action,
                    "latency_ms": trace.latency_ms,
                    "is_crisis": trace.safety_result.risk_level == RiskLevel.CRISIS,
                    "fallback_used": (
                        trace.safety_result.llm_usage.fallback_used
                        or trace.intent_result.llm_usage.fallback_used
                    ),
                    "hook_blocked": any(
                        error.startswith("before_action_blocked:")
                        for error in trace.errors
                    ),
                    "memory_write_blocked": any(
                        error.startswith("before_memory_write_blocked:")
                        for error in trace.errors
                    ),
                    "product_boundary_eval": _product_boundary_eval_label(trace),
                    "created_at": trace.created_at,
                },
            )

    def record_runtime_event(self, event_name: str, *, count: int = 1) -> None:
        """Persist non-identifying runtime events."""
        with self.engine.begin() as connection:
            for _ in range(max(0, count)):
                connection.execute(
                    text(
                        """INSERT INTO harness_runtime_metric_events
                        (event_name, created_at)
                        VALUES (:event_name, now())"""
                    ),
                    {"event_name": event_name},
                )

    def snapshot(self) -> HarnessMetricsSnapshot:
        """Return aggregate metrics from persisted metric events."""
        snapshot = HarnessMetricsSnapshot()
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT intent, risk_level, selected_agent, permission_action, latency_ms,
                    is_crisis, fallback_used, hook_blocked, memory_write_blocked,
                    product_boundary_eval
                    FROM harness_metric_events"""
                )
            ).mappings().all()
            runtime_rows = connection.execute(
                text(
                    """SELECT event_name, COUNT(*) AS event_count
                    FROM harness_runtime_metric_events
                    GROUP BY event_name"""
                )
            ).mappings().all()
        for row in rows:
            _record_row_into_snapshot(snapshot, row)
        for row in runtime_rows:
            snapshot.runtime_event_counts[row["event_name"]] = int(row["event_count"])
        return snapshot

    def reset(self) -> None:
        """Clear persisted metrics for tests or local demo resets."""
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM harness_metric_events"))
            connection.execute(text("DELETE FROM harness_runtime_metric_events"))
