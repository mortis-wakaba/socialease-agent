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


class FakeRunLock:
    """Controllable cleanup lock for scheduler behavior tests."""

    backend_name = "fake"

    def __init__(self, *, acquired: bool = True) -> None:
        self.acquired = acquired
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        return self.acquired

    def release(self) -> None:
        self.release_calls += 1


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


def test_cleanup_scheduler_skips_when_another_replica_holds_lock() -> None:
    service = FakeRetentionService()
    run_lock = FakeRunLock(acquired=False)
    scheduler = CleanupScheduler(
        service,  # type: ignore[arg-type]
        CleanupSchedulerConfig(),
        run_lock=run_lock,
    )

    result = scheduler.run_once()

    assert result == RetentionResult(expired_protocols=0, cancelled_intervention_plans=0)
    assert service.calls == []
    assert run_lock.acquire_calls == 1
    assert run_lock.release_calls == 0
    assert scheduler.last_run_skipped_due_to_lock is True


def test_cleanup_scheduler_releases_lock_after_failure() -> None:
    service = FakeRetentionService(fail=True)
    run_lock = FakeRunLock()
    scheduler = CleanupScheduler(
        service,  # type: ignore[arg-type]
        CleanupSchedulerConfig(),
        run_lock=run_lock,
    )

    try:
        scheduler.run_once()
    except RuntimeError:
        pass
    else:  # pragma: no cover - assertion guard
        raise AssertionError("cleanup failure should propagate from run_once")

    assert run_lock.acquire_calls == 1
    assert run_lock.release_calls == 1
