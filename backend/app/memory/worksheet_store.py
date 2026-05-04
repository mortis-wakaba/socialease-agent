"""In-memory worksheet store with a replaceable repository shape."""

from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from app.models_worksheet import (
    WORKSHEET_DISCLAIMER,
    WorksheetFields,
    WorksheetRecord,
)
from app.models_knowledge import Citation


class WorksheetStore:
    """Store CBT-style worksheets in memory for the MVP."""

    def __init__(self) -> None:
        self._worksheets: dict[str, WorksheetRecord] = {}
        self._lock = Lock()

    def create(
        self,
        user_id: str,
        source_message: str,
        fields: WorksheetFields,
        citations: list[Citation],
        missing_fields: list[str],
        gentle_followup_questions: list[str],
    ) -> WorksheetRecord:
        """Create and store a worksheet record."""
        record = WorksheetRecord(
            worksheet_id=str(uuid4()),
            user_id=user_id,
            source_message=source_message,
            fields=fields,
            citations=citations,
            disclaimer=WORKSHEET_DISCLAIMER,
            missing_fields=missing_fields,
            gentle_followup_questions=gentle_followup_questions,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._worksheets[record.worksheet_id] = record
        return record

    def get(self, worksheet_id: str) -> WorksheetRecord | None:
        """Return a worksheet by id, if present."""
        with self._lock:
            return self._worksheets.get(worksheet_id)


worksheet_store = WorksheetStore()
