"""Tests for the standalone cleanup scheduler."""

from datetime import datetime, timezone

from app.jobs.cleanup_scheduler import (
    CleanupScheduler,
    CleanupSchedulerConfig,
    config_from_env,
)
from app.services.retention_service import RetentionResult


class FakeRetentionService:
    """Test double for retention cleanup."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[datetime | None, int]] = []

    def run_once(
        self,
        *,
        now: datetime | None = None,
        abandoned_plan_minutes: int = 60,
        trace_retention_days: int = 30,
        protocol_retention_days: int = 30,
    ) -> RetentionResult:
        """Record a cleanup call and return aggregate counts."""
        self.calls.append((now, abandoned_plan_minutes))
        if self.fail:
            raise RuntimeError("cleanup failed")
        return RetentionResult(
            expired_protocols=2,
            cancelled_intervention_plans=1,
            deleted_raw_traces=3,
            deleted_protocol_records=4,
            deleted_intervention_plans=5,
        )


def test_cleanup_scheduler_run_once_calls_retention_service() -> None:
    service = FakeRetentionService()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    scheduler = CleanupScheduler(
        service,  # type: ignore[arg-type]
        CleanupSchedulerConfig(abandoned_plan_minutes=45),
        now_fn=lambda: now,
    )

    result = scheduler.run_once()

    assert result.expired_protocols == 2
    assert result.cancelled_intervention_plans == 1
    assert result.deleted_raw_traces == 3
    assert result.deleted_protocol_records == 4
    assert result.deleted_intervention_plans == 5
    assert service.calls == [(now, 45)]


def test_cleanup_scheduler_dry_run_does_not_call_service() -> None:
    service = FakeRetentionService()
    scheduler = CleanupScheduler(
        service,  # type: ignore[arg-type]
        CleanupSchedulerConfig(dry_run=True),
    )

    result = scheduler.run_once()

    assert result == RetentionResult(
        expired_protocols=0,
        cancelled_intervention_plans=0,
        deleted_raw_traces=0,
        deleted_protocol_records=0,
        deleted_intervention_plans=0,
    )
    assert service.calls == []


def test_cleanup_scheduler_run_forever_respects_max_runs_and_interval() -> None:
    service = FakeRetentionService()
    sleeps: list[float] = []
    scheduler = CleanupScheduler(
        service,  # type: ignore[arg-type]
        CleanupSchedulerConfig(interval_seconds=3),
        sleep_fn=sleeps.append,
    )

    runs = scheduler.run_forever(max_runs=3)

    assert runs == 3
    assert len(service.calls) == 3
    assert sleeps == [3, 3]


def test_cleanup_scheduler_continues_after_iteration_error() -> None:
    service = FakeRetentionService(fail=True)
    sleeps: list[float] = []
    scheduler = CleanupScheduler(
        service,  # type: ignore[arg-type]
        CleanupSchedulerConfig(interval_seconds=1),
        sleep_fn=sleeps.append,
    )

    runs = scheduler.run_forever(max_runs=2)

    assert runs == 2
    assert len(service.calls) == 2
    assert sleeps == [1]


def test_cleanup_scheduler_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("SOCIALEASE_CLEANUP_INTERVAL_SECONDS", "12")
    monkeypatch.setenv("SOCIALEASE_ABANDONED_PLAN_MINUTES", "34")
    monkeypatch.setenv("SOCIALEASE_TRACE_RETENTION_DAYS", "56")
    monkeypatch.setenv("SOCIALEASE_PROTOCOL_RETENTION_DAYS", "78")
    monkeypatch.setenv("SOCIALEASE_CLEANUP_DRY_RUN", "true")

    config = config_from_env()

    assert config.interval_seconds == 12
    assert config.abandoned_plan_minutes == 34
    assert config.trace_retention_days == 56
    assert config.protocol_retention_days == 78
    assert config.dry_run is True
