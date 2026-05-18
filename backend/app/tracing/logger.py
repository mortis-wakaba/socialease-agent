"""Trace logger backed by a replaceable repository."""

from app.db.repositories import SQLiteTraceRepository, TraceRepository
from app.models import TraceRecord


class TraceLogger:
    """Persist workflow traces through the configured repository."""

    def __init__(self, repository: TraceRepository | None = None) -> None:
        self.repository = repository or SQLiteTraceRepository()

    def save(self, record: TraceRecord) -> TraceRecord:
        """Persist a trace record and return it."""
        return self.repository.save(record)

    def get(self, run_id: str) -> TraceRecord | None:
        """Return a trace record by run id, if present."""
        return self.repository.get(run_id)


trace_logger = TraceLogger()
