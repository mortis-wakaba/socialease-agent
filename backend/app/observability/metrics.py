"""Process-safe aggregate metrics backend for harness traces."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from threading import Lock

from app.db.engine import connect
from app.db.session import initialize_database
from app.models import RiskLevel, TraceRecord


@dataclass
class HarnessMetricsSnapshot:
    """Aggregate non-identifying metrics captured from harness traces."""

    total_runs: int = 0
    crisis_runs: int = 0
    fallback_runs: int = 0
    hook_blocked_runs: int = 0
    memory_write_blocked_runs: int = 0
    total_latency_ms: float = 0.0
    latency_values_ms: list[float] = field(default_factory=list)
    intent_counts: dict[str, int] = field(default_factory=dict)
    risk_counts: dict[str, int] = field(default_factory=dict)
    selected_agent_counts: dict[str, int] = field(default_factory=dict)
    permission_counts: dict[str, int] = field(default_factory=dict)
    product_boundary_eval_counts: dict[str, int] = field(default_factory=dict)
    runtime_event_counts: dict[str, int] = field(default_factory=dict)

    @property
    def rate_limit_hits(self) -> int:
        """Return the number of API rate-limit rejections."""
        return self.runtime_event_counts.get("rate_limit_hit", 0)

    @property
    def llm_concurrency_saturation(self) -> int:
        """Return the number of LLM concurrency-capacity rejections."""
        return self.runtime_event_counts.get("llm_concurrency_saturation", 0)

    @property
    def slow_request_count(self) -> int:
        """Return the number of requests over the configured slow threshold."""
        return self.runtime_event_counts.get("slow_request", 0)

    @property
    def memory_export_count(self) -> int:
        """Return the number of user-owned memory exports."""
        return self.runtime_event_counts.get("memory_export", 0)

    @property
    def memory_delete_count(self) -> int:
        """Return the number of user-owned memory deletions."""
        return self.runtime_event_counts.get("memory_delete", 0)

    @property
    def memory_preferences_saved_count(self) -> int:
        """Return the number of explicit long-term preference saves."""
        return self.runtime_event_counts.get("memory_preferences_saved", 0)

    @property
    def memory_preferences_disabled_count(self) -> int:
        """Return the number of long-term preference disable actions."""
        return self.runtime_event_counts.get("memory_preferences_disabled", 0)

    @property
    def auth_rate_limit_hits(self) -> int:
        """Return the number of auth endpoint rate-limit rejections."""
        return self.runtime_event_counts.get("auth_rate_limit_hit", 0)

    @property
    def auth_failed_login_count(self) -> int:
        """Return the number of failed login attempts."""
        return self.runtime_event_counts.get("auth_failed_login", 0)

    @property
    def auth_lockout_count(self) -> int:
        """Return the number of temporary auth lockouts."""
        return self.runtime_event_counts.get("auth_lockout", 0)

    @property
    def average_latency_ms(self) -> float:
        """Return mean latency for captured runs."""
        if self.total_runs == 0:
            return 0.0
        return self.total_latency_ms / self.total_runs

    @property
    def latency_p50_ms(self) -> float:
        """Return approximate p50 latency for captured runs."""
        return _percentile(self.latency_values_ms, 50)

    @property
    def latency_p95_ms(self) -> float:
        """Return approximate p95 latency for captured runs."""
        return _percentile(self.latency_values_ms, 95)


class MetricsRepository:
    """Persistence contract for aggregate harness metrics."""

    def record_trace(self, trace: TraceRecord) -> None:
        """Record one non-identifying metrics event from a trace."""
        raise NotImplementedError

    def record_runtime_event(self, event_name: str, *, count: int = 1) -> None:
        """Record one non-identifying runtime event outside a trace."""
        raise NotImplementedError

    def snapshot(self) -> HarnessMetricsSnapshot:
        """Return aggregate metrics."""
        raise NotImplementedError

    def reset(self) -> None:
        """Clear metrics for tests or local demo resets."""
        raise NotImplementedError


class InMemoryMetricsRepository(MetricsRepository):
    """In-memory metrics backend for isolated unit tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = HarnessMetricsSnapshot()

    def record_trace(self, trace: TraceRecord) -> None:
        """Record one trace into memory."""
        with self._lock:
            _record_into_snapshot(self._snapshot, trace)

    def record_runtime_event(self, event_name: str, *, count: int = 1) -> None:
        """Record one runtime event into memory."""
        with self._lock:
            _increment_by(self._snapshot.runtime_event_counts, event_name, count)

    def snapshot(self) -> HarnessMetricsSnapshot:
        """Return a copy of in-memory metrics."""
        with self._lock:
            return _copy_snapshot(self._snapshot)

    def reset(self) -> None:
        """Clear in-memory metrics."""
        with self._lock:
            self._snapshot = HarnessMetricsSnapshot()


class SQLiteMetricsRepository(MetricsRepository):
    """SQLite-backed metrics backend storing only aggregate-safe fields."""

    def __init__(self) -> None:
        initialize_database()

    def record_trace(self, trace: TraceRecord) -> None:
        """Persist one non-identifying metrics event."""
        _ensure_runtime_metric_table()
        with connect() as connection:
            connection.execute(
                """INSERT INTO harness_metric_events
                (intent, risk_level, selected_agent, permission_action, latency_ms,
                is_crisis, fallback_used, hook_blocked, memory_write_blocked,
                product_boundary_eval, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace.intent_result.intent.value,
                    trace.safety_result.risk_level.value,
                    trace.selected_agent,
                    trace.permission_action,
                    trace.latency_ms,
                    int(trace.safety_result.risk_level == RiskLevel.CRISIS),
                    int(
                        trace.safety_result.llm_usage.fallback_used
                        or trace.intent_result.llm_usage.fallback_used
                    ),
                    int(any(error.startswith("before_action_blocked:") for error in trace.errors)),
                    int(
                        any(
                            error.startswith("before_memory_write_blocked:")
                            for error in trace.errors
                        )
                    ),
                    _product_boundary_eval_label(trace),
                    trace.created_at.isoformat(),
                ),
            )

    def record_runtime_event(self, event_name: str, *, count: int = 1) -> None:
        """Persist a non-identifying runtime event."""
        _ensure_runtime_metric_table()
        with connect() as connection:
            for _ in range(max(0, count)):
                connection.execute(
                    """INSERT INTO harness_runtime_metric_events
                    (event_name, created_at)
                    VALUES (?, ?)""",
                    (event_name, datetime.now(timezone.utc).isoformat()),
                )

    def snapshot(self) -> HarnessMetricsSnapshot:
        """Return aggregate metrics from persisted metric events."""
        _ensure_runtime_metric_table()
        snapshot = HarnessMetricsSnapshot()
        with connect() as connection:
            rows = connection.execute(
                """SELECT intent, risk_level, selected_agent, permission_action, latency_ms,
                is_crisis, fallback_used, hook_blocked, memory_write_blocked,
                product_boundary_eval
                FROM harness_metric_events"""
            ).fetchall()
            runtime_rows = connection.execute(
                """SELECT event_name, COUNT(*) AS event_count
                FROM harness_runtime_metric_events
                GROUP BY event_name"""
            ).fetchall()
        for row in rows:
            _record_row_into_snapshot(snapshot, row)
        for row in runtime_rows:
            _increment_by(snapshot.runtime_event_counts, row["event_name"], int(row["event_count"]))
        return snapshot

    def reset(self) -> None:
        """Clear persisted metrics for tests or local demo resets."""
        _ensure_runtime_metric_table()
        with connect() as connection:
            connection.execute("DELETE FROM harness_metric_events")
            connection.execute("DELETE FROM harness_runtime_metric_events")


def _record_into_snapshot(snapshot: HarnessMetricsSnapshot, trace: TraceRecord) -> None:
    """Update a snapshot from one trace without storing identifying fields."""
    snapshot.total_runs += 1
    snapshot.total_latency_ms += trace.latency_ms
    snapshot.latency_values_ms.append(trace.latency_ms)
    _increment(snapshot.intent_counts, trace.intent_result.intent.value)
    _increment(snapshot.risk_counts, trace.safety_result.risk_level.value)
    _increment(snapshot.selected_agent_counts, trace.selected_agent)
    if trace.permission_action:
        _increment(snapshot.permission_counts, trace.permission_action)
    if trace.safety_result.risk_level == RiskLevel.CRISIS:
        snapshot.crisis_runs += 1
    if trace.safety_result.llm_usage.fallback_used or trace.intent_result.llm_usage.fallback_used:
        snapshot.fallback_runs += 1
    if any(error.startswith("before_action_blocked:") for error in trace.errors):
        snapshot.hook_blocked_runs += 1
    if any(error.startswith("before_memory_write_blocked:") for error in trace.errors):
        snapshot.memory_write_blocked_runs += 1
    _increment(snapshot.product_boundary_eval_counts, _product_boundary_eval_label(trace))


def _record_row_into_snapshot(snapshot: HarnessMetricsSnapshot, row) -> None:
    """Update a snapshot from one persisted SQLite row."""
    snapshot.total_runs += 1
    latency = float(row["latency_ms"])
    snapshot.total_latency_ms += latency
    snapshot.latency_values_ms.append(latency)
    _increment(snapshot.intent_counts, row["intent"])
    _increment(snapshot.risk_counts, row["risk_level"])
    _increment(snapshot.selected_agent_counts, row["selected_agent"])
    if row["permission_action"]:
        _increment(snapshot.permission_counts, row["permission_action"])
    snapshot.crisis_runs += int(row["is_crisis"])
    snapshot.fallback_runs += int(row["fallback_used"])
    snapshot.hook_blocked_runs += int(row["hook_blocked"])
    snapshot.memory_write_blocked_runs += int(row["memory_write_blocked"])
    _increment(snapshot.product_boundary_eval_counts, row["product_boundary_eval"])


def _copy_snapshot(snapshot: HarnessMetricsSnapshot) -> HarnessMetricsSnapshot:
    """Return a deep-enough copy for callers."""
    return HarnessMetricsSnapshot(
        total_runs=snapshot.total_runs,
        crisis_runs=snapshot.crisis_runs,
        fallback_runs=snapshot.fallback_runs,
        hook_blocked_runs=snapshot.hook_blocked_runs,
        memory_write_blocked_runs=snapshot.memory_write_blocked_runs,
        total_latency_ms=snapshot.total_latency_ms,
        latency_values_ms=list(snapshot.latency_values_ms),
        intent_counts=dict(snapshot.intent_counts),
        risk_counts=dict(snapshot.risk_counts),
        selected_agent_counts=dict(snapshot.selected_agent_counts),
        permission_counts=dict(snapshot.permission_counts),
        product_boundary_eval_counts=dict(snapshot.product_boundary_eval_counts),
        runtime_event_counts=dict(snapshot.runtime_event_counts),
    )


def _product_boundary_eval_label(trace: TraceRecord) -> str:
    """Return a coarse product-boundary label without storing trace content."""
    if not trace.product_safe:
        return "unsafe_trace"
    if trace.safety_result.risk_level == RiskLevel.CRISIS and trace.selected_agent == "crisis_escalation":
        return "crisis_escalated"
    if trace.permission_action in {"ask_consent", "block", "escalate"}:
        return f"permission_{trace.permission_action}"
    if trace.privacy_summary is not None:
        fields = trace.privacy_summary.fields
        if any(field.minimized for field in fields):
            return "privacy_minimized"
    return "safe_standard"


def _increment(counts: dict[str, int], key: str) -> None:
    """Increment one counter in a mutable dictionary."""
    counts[key] = counts.get(key, 0) + 1


def _increment_by(counts: dict[str, int], key: str, value: int) -> None:
    """Increment one counter by a positive value."""
    if value <= 0:
        return
    counts[key] = counts.get(key, 0) + value


def _ensure_runtime_metric_table() -> None:
    """Create the runtime metrics table for existing local SQLite databases."""
    with connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS harness_runtime_metric_events (
            metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_harness_runtime_metric_events_name
            ON harness_runtime_metric_events(event_name)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_harness_runtime_metric_events_created_at
            ON harness_runtime_metric_events(created_at)"""
        )


def _percentile(values: list[float], percentile: int) -> float:
    """Return a nearest-rank percentile for a small metric window."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = round((percentile / 100) * (len(sorted_values) - 1))
    return sorted_values[index]


def snapshot_to_json(snapshot: HarnessMetricsSnapshot) -> str:
    """Serialize a metrics snapshot for scripts or diagnostics."""
    return json.dumps(
        {
            "total_runs": snapshot.total_runs,
            "crisis_runs": snapshot.crisis_runs,
            "fallback_runs": snapshot.fallback_runs,
            "hook_blocked_runs": snapshot.hook_blocked_runs,
            "memory_write_blocked_runs": snapshot.memory_write_blocked_runs,
            "average_latency_ms": snapshot.average_latency_ms,
            "latency_p50_ms": snapshot.latency_p50_ms,
            "latency_p95_ms": snapshot.latency_p95_ms,
            "intent_counts": snapshot.intent_counts,
            "risk_counts": snapshot.risk_counts,
            "selected_agent_counts": snapshot.selected_agent_counts,
            "permission_counts": snapshot.permission_counts,
            "product_boundary_eval_counts": snapshot.product_boundary_eval_counts,
            "runtime_event_counts": snapshot.runtime_event_counts,
            "rate_limit_hits": snapshot.rate_limit_hits,
            "llm_concurrency_saturation": snapshot.llm_concurrency_saturation,
            "slow_request_count": snapshot.slow_request_count,
            "memory_export_count": snapshot.memory_export_count,
            "memory_delete_count": snapshot.memory_delete_count,
            "memory_preferences_saved_count": snapshot.memory_preferences_saved_count,
            "memory_preferences_disabled_count": snapshot.memory_preferences_disabled_count,
            "auth_rate_limit_hits": snapshot.auth_rate_limit_hits,
            "auth_failed_login_count": snapshot.auth_failed_login_count,
            "auth_lockout_count": snapshot.auth_lockout_count,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
