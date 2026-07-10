"""Standalone cleanup scheduler for retention jobs."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.retention_service import RetentionResult, RetentionService, retention_service


LOGGER = logging.getLogger("socialease.cleanup_scheduler")


@dataclass(frozen=True)
class CleanupSchedulerConfig:
    """Runtime settings for the cleanup scheduler."""

    interval_seconds: int = 15 * 60
    abandoned_plan_minutes: int = 60
    trace_retention_days: int = 30
    protocol_retention_days: int = 30
    dry_run: bool = False


class CleanupScheduler:
    """Run retention cleanup outside the FastAPI request process."""

    def __init__(
        self,
        service: RetentionService,
        config: CleanupSchedulerConfig,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.config = config
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.logger = logger or LOGGER
        self._stop_requested = False

    def request_stop(self) -> None:
        """Request a graceful stop after the current iteration."""
        self._stop_requested = True

    def run_once(self) -> RetentionResult:
        """Run one cleanup iteration and log aggregate counts only."""
        if self.config.dry_run:
            result = RetentionResult(
                expired_protocols=0,
                cancelled_intervention_plans=0,
                deleted_raw_traces=0,
                deleted_protocol_records=0,
                deleted_intervention_plans=0,
            )
            self._log_result(result, dry_run=True)
            return result
        result = self.service.run_once(
            now=self.now_fn(),
            abandoned_plan_minutes=self.config.abandoned_plan_minutes,
            trace_retention_days=self.config.trace_retention_days,
            protocol_retention_days=self.config.protocol_retention_days,
        )
        self._log_result(result, dry_run=False)
        return result

    def run_forever(self, *, max_runs: int | None = None) -> int:
        """Run cleanup repeatedly until stopped or max_runs is reached."""
        runs = 0
        while not self._stop_requested:
            try:
                self.run_once()
            except Exception:
                self.logger.exception("cleanup_scheduler_iteration_failed")
            runs += 1
            if max_runs is not None and runs >= max_runs:
                break
            self.sleep_fn(self.config.interval_seconds)
        return runs

    def _log_result(self, result: RetentionResult, *, dry_run: bool) -> None:
        """Log non-identifying cleanup counts."""
        self.logger.info(
            "cleanup_scheduler_iteration_completed "
            "dry_run=%s expired_protocols=%s cancelled_intervention_plans=%s "
            "deleted_raw_traces=%s deleted_protocol_records=%s "
            "deleted_intervention_plans=%s trace_retention_days=%s "
            "protocol_retention_days=%s",
            dry_run,
            result.expired_protocols,
            result.cancelled_intervention_plans,
            result.deleted_raw_traces,
            result.deleted_protocol_records,
            result.deleted_intervention_plans,
            self.config.trace_retention_days,
            self.config.protocol_retention_days,
        )


def config_from_env() -> CleanupSchedulerConfig:
    """Return cleanup scheduler configuration from environment variables."""
    return CleanupSchedulerConfig(
        interval_seconds=int(os.getenv("SOCIALEASE_CLEANUP_INTERVAL_SECONDS", "900")),
        abandoned_plan_minutes=int(os.getenv("SOCIALEASE_ABANDONED_PLAN_MINUTES", "60")),
        trace_retention_days=int(os.getenv("SOCIALEASE_TRACE_RETENTION_DAYS", "30")),
        protocol_retention_days=int(os.getenv("SOCIALEASE_PROTOCOL_RETENTION_DAYS", "30")),
        dry_run=os.getenv("SOCIALEASE_CLEANUP_DRY_RUN", "false").lower() in {"1", "true", "yes"},
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the cleanup scheduler CLI parser."""
    parser = argparse.ArgumentParser(description="Run SocialEase retention cleanup jobs.")
    parser.add_argument("--run-once", action="store_true", help="Run cleanup once and exit.")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        help="Seconds between cleanup iterations.",
    )
    parser.add_argument(
        "--abandoned-plan-minutes",
        type=int,
        default=None,
        help="Cancel pending-consent plans older than this many minutes.",
    )
    parser.add_argument(
        "--trace-retention-days",
        type=int,
        default=None,
        help="Documented trace retention window for cleanup reporting.",
    )
    parser.add_argument(
        "--protocol-retention-days",
        type=int,
        default=None,
        help="Delete terminal protocol and intervention-plan records older than this many days.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log the iteration without cleanup.")
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Run at most this many iterations; intended for tests or one-off jobs.",
    )
    return parser


def main() -> None:
    """Run the cleanup scheduler CLI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = build_parser()
    args = parser.parse_args()
    env_config = config_from_env()
    config = CleanupSchedulerConfig(
        interval_seconds=args.interval_seconds or env_config.interval_seconds,
        abandoned_plan_minutes=args.abandoned_plan_minutes or env_config.abandoned_plan_minutes,
        trace_retention_days=args.trace_retention_days or env_config.trace_retention_days,
        protocol_retention_days=(
            args.protocol_retention_days or env_config.protocol_retention_days
        ),
        dry_run=args.dry_run or env_config.dry_run,
    )
    scheduler = CleanupScheduler(retention_service, config)

    def _handle_stop(_signum, _frame) -> None:
        scheduler.request_stop()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if args.run_once:
        scheduler.run_once()
        return
    scheduler.run_forever(max_runs=args.max_runs)


if __name__ == "__main__":
    main()
