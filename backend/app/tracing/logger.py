"""Trace logger backed by a replaceable repository."""

from app.db.factory import repository_factory
from app.db.repositories import TraceRepository
from app.models import TraceRecord
from app.tracing.sanitizer import TraceSanitizer, trace_sanitizer


class TraceLogger:
    """Persist workflow traces through the configured repository."""

    def __init__(
        self,
        repository: TraceRepository | None = None,
        sanitizer: TraceSanitizer | None = None,
    ) -> None:
        self.repository = repository or repository_factory().trace_repository()
        self.sanitizer = sanitizer or trace_sanitizer

    def prepare(self, record: TraceRecord) -> TraceRecord:
        """Return the privacy-sanitized record that is safe to persist or return."""
        return self.sanitizer.sanitize(record)

    async def save(self, record: TraceRecord) -> TraceRecord:
        """Sanitize and persist a trace record, then return the persisted form."""
        return await self.repository.save(self.prepare(record))

    async def get(self, run_id: str) -> TraceRecord | None:
        """Return a trace record by run id, if present."""
        return await self.repository.get(run_id)

    async def list_recent(self, limit: int = 100) -> list[TraceRecord]:
        """Return recent trace records for lightweight metrics."""
        return await self.repository.list_recent(limit=limit)


trace_logger = TraceLogger()
