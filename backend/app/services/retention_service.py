"""Retention and cleanup jobs for local demo persistence."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from app.db.config import database_settings
from app.db.engine import connect
from app.db.factory import repository_factory
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.protocols.service import ProtocolService


@dataclass(frozen=True)
class RetentionResult:
    """Counts returned by one retention cleanup run."""

    expired_protocols: int
    cancelled_intervention_plans: int
    deleted_raw_traces: int = 0
    deleted_protocol_records: int = 0
    deleted_intervention_plans: int = 0


class RetentionService:
    """Run explicit cleanup tasks without process-local background assumptions."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or database_settings().database_url
        self.provider = resolve_database_provider(self.database_url)
        factory = repository_factory(self.database_url)
        self.protocol_service = ProtocolService(store=factory.protocol_repository())
        self.intervention_plan_repository = factory.intervention_plan_repository()

    def expire_pending_protocols(self, *, now: datetime | None = None) -> int:
        """Expire pending protocols that are past their expiration timestamp."""
        return self.protocol_service.expire_pending_protocols(now=now)

    def cancel_abandoned_intervention_plans(
        self,
        *,
        older_than_minutes: int = 60,
        now: datetime | None = None,
    ) -> int:
        """Cancel pending-consent plans abandoned before the retention cutoff."""
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(minutes=older_than_minutes)
        return self.intervention_plan_repository.cancel_pending_consent_before(cutoff)

    def delete_trace_records_before(self, cutoff: datetime) -> int:
        """Delete trace records older than the retention cutoff."""
        if self.provider == DatabaseProvider.POSTGRES:
            return self._delete_postgres_rows(
                """DELETE FROM runs
                WHERE created_at <= :cutoff""",
                {"cutoff": cutoff},
            )
        with connect() as connection:
            cursor = connection.execute(
                "DELETE FROM runs WHERE created_at <= ?",
                (cutoff.isoformat(),),
            )
            return cursor.rowcount

    def delete_terminal_protocols_before(self, cutoff: datetime) -> int:
        """Delete terminal protocol records older than the retention cutoff."""
        terminal_statuses = ("expired", "rejected", "consumed")
        if self.provider == DatabaseProvider.POSTGRES:
            return self._delete_postgres_rows(
                """DELETE FROM protocols
                WHERE status IN ('expired', 'rejected', 'consumed')
                  AND updated_at <= :cutoff""",
                {"cutoff": cutoff},
            )
        with connect() as connection:
            cursor = connection.execute(
                """DELETE FROM protocols
                WHERE status IN (?, ?, ?) AND updated_at <= ?""",
                (*terminal_statuses, cutoff.isoformat()),
            )
            return cursor.rowcount

    def delete_terminal_intervention_plans_before(self, cutoff: datetime) -> int:
        """Delete terminal intervention plans older than the retention cutoff."""
        terminal_statuses = ("completed", "cancelled", "blocked")
        if self.provider == DatabaseProvider.POSTGRES:
            return self._delete_postgres_rows(
                """DELETE FROM intervention_plans
                WHERE status IN ('completed', 'cancelled', 'blocked')
                  AND updated_at <= :cutoff""",
                {"cutoff": cutoff},
            )
        with connect() as connection:
            cursor = connection.execute(
                """DELETE FROM intervention_plans
                WHERE status IN (?, ?, ?) AND updated_at <= ?""",
                (*terminal_statuses, cutoff.isoformat()),
            )
            return cursor.rowcount

    def run_once(
        self,
        *,
        now: datetime | None = None,
        abandoned_plan_minutes: int = 60,
        trace_retention_days: int = 30,
        protocol_retention_days: int = 30,
    ) -> RetentionResult:
        """Run all retention jobs once and return affected row counts."""
        current = now or datetime.now(timezone.utc)
        trace_cutoff = current - timedelta(days=trace_retention_days)
        protocol_cutoff = current - timedelta(days=protocol_retention_days)
        return RetentionResult(
            expired_protocols=self.expire_pending_protocols(now=current),
            cancelled_intervention_plans=self.cancel_abandoned_intervention_plans(
                older_than_minutes=abandoned_plan_minutes,
                now=current,
            ),
            deleted_raw_traces=self.delete_trace_records_before(trace_cutoff),
            deleted_protocol_records=self.delete_terminal_protocols_before(protocol_cutoff),
            deleted_intervention_plans=self.delete_terminal_intervention_plans_before(
                protocol_cutoff
            ),
        )

    def _delete_postgres_rows(self, statement: str, params: dict[str, object]) -> int:
        """Delete PostgreSQL rows and return affected count."""
        engine = create_engine(self.database_url, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                result = connection.execute(text(statement), params)
                return result.rowcount or 0
        finally:
            engine.dispose()


retention_service = RetentionService()
