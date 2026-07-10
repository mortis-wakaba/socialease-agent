"""Trace logger backed by a replaceable repository."""

from app.db.factory import repository_factory
from app.db.repositories import TraceRepository
from app.models import TraceRecord


class TraceLogger:
    """Persist workflow traces through the configured repository."""

    def __init__(self, repository: TraceRepository | None = None) -> None:
        self.repository = repository or repository_factory().trace_repository()

    def save(self, record: TraceRecord) -> TraceRecord:
        """Persist a trace record and return it."""
        return self.repository.save(record)

    def get(self, run_id: str) -> TraceRecord | None:
        """Return a trace record by run id, if present."""
        return self.repository.get(run_id)

    def list_recent(self, limit: int = 100) -> list[TraceRecord]:
        """Return recent trace records for lightweight metrics."""
        return self.repository.list_recent(limit=limit)


trace_logger = TraceLogger()
