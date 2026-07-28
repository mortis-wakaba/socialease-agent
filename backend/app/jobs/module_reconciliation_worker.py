"""Lease-based background reconciliation for durable module-start jobs."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import logging
import os
import signal
from uuid import uuid4

from app.calendar.outbox import CalendarActionOutbox
from app.calendar.outbox_processor import CalendarOutboxProcessor
from app.conversation.repository import ConversationRepository
from app.db.factory import repository_factory
from app.observability.runtime_events import record_runtime_event
from app.services.conversation_runtime import conversation_service


LOGGER = logging.getLogger("socialease.module_reconciliation")


@dataclass(frozen=True)
class ReconciliationConfig:
    """Bounded worker scheduling and lease settings."""

    interval_seconds: float = 2.0
    batch_size: int = 20
    lease_seconds: int = 60


class ModuleReconciliationWorker:
    """Claim and replay module jobs safely across multiple worker replicas."""

    def __init__(
        self,
        repository: ConversationRepository,
        *,
        config: ReconciliationConfig,
        worker_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.worker_id = worker_id or f"module-worker:{uuid4().hex}"
        self.calendar_processor = CalendarOutboxProcessor(
            outbox=CalendarActionOutbox(),
            worker_id=f"{self.worker_id}:calendar",
        )
        self._stop_requested = False

    def request_stop(self) -> None:
        """Request graceful shutdown after the current batch."""
        self._stop_requested = True

    async def run_once(self) -> int:
        """Process one lease-owned batch and return the number claimed."""
        jobs = await self.repository.claim_due_module_starts(
            worker_id=self.worker_id,
            limit=self.config.batch_size,
            lease_seconds=self.config.lease_seconds,
        )
        for job in jobs:
            try:
                await conversation_service().reconcile_module_start(job)
            except Exception:
                await record_runtime_event("module_outbox_retry")
                if job.attempt_count >= job.max_attempts:
                    await record_runtime_event("module_outbox_dead_letter")
                LOGGER.exception(
                    "module_reconciliation_failed module_run_id=%s attempt=%s",
                    job.module_run_id,
                    job.attempt_count,
                )
            else:
                await record_runtime_event("module_outbox_completed")
        calendar_count = await self.calendar_processor.process_due(
            limit=self.config.batch_size
        )
        return len(jobs) + calendar_count

    async def run_forever(self, *, max_runs: int | None = None) -> None:
        """Poll until stopped, preserving leases when a process terminates."""
        runs = 0
        while not self._stop_requested:
            await self.run_once()
            runs += 1
            if max_runs is not None and runs >= max_runs:
                return
            await asyncio.sleep(self.config.interval_seconds)


def config_from_env() -> ReconciliationConfig:
    """Load bounded worker settings from environment variables."""
    return ReconciliationConfig(
        interval_seconds=max(
            0.1,
            float(os.getenv("SOCIALEASE_OUTBOX_INTERVAL_SECONDS", "2")),
        ),
        batch_size=max(
            1,
            min(100, int(os.getenv("SOCIALEASE_OUTBOX_BATCH_SIZE", "20"))),
        ),
        lease_seconds=max(
            10,
            int(os.getenv("SOCIALEASE_OUTBOX_LEASE_SECONDS", "60")),
        ),
    )


def main() -> None:
    """Run the standalone reconciliation process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    worker = ModuleReconciliationWorker(
        repository_factory().conversation_repository(),
        config=config_from_env(),
    )

    def stop(_signum, _frame) -> None:
        worker.request_stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    asyncio.run(worker.run_once() if args.run_once else worker.run_forever())


if __name__ == "__main__":
    main()
