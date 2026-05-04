"""In-memory trace logger for MVP workflow runs."""

from threading import Lock

from app.models import TraceRecord


class TraceLogger:
    """Store workflow traces in memory for local development."""

    def __init__(self) -> None:
        self._records: dict[str, TraceRecord] = {}
        self._lock = Lock()

    def save(self, record: TraceRecord) -> TraceRecord:
        """Persist a trace record and return it."""
        with self._lock:
            self._records[record.run_id] = record
        return record

    def get(self, run_id: str) -> TraceRecord | None:
        """Return a trace record by run id, if present."""
        with self._lock:
            return self._records.get(run_id)


trace_logger = TraceLogger()

