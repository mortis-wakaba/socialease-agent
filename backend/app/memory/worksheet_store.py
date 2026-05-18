"""Worksheet store backed by a replaceable repository."""

from datetime import datetime, timezone
from uuid import uuid4

from app.db.repositories import SQLiteWorksheetRepository, WorksheetRepository
from app.models_worksheet import (
    WORKSHEET_DISCLAIMER,
    WorksheetFields,
    WorksheetRecord,
)
from app.models_knowledge import Citation


class WorksheetStore:
    """Coordinate worksheet creation and repository persistence."""

    def __init__(self, repository: WorksheetRepository | None = None) -> None:
        self.repository = repository or SQLiteWorksheetRepository()

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
        return self.repository.save(record)

    def get(self, worksheet_id: str) -> WorksheetRecord | None:
        """Return a worksheet by id, if present."""
        return self.repository.get(worksheet_id)


worksheet_store = WorksheetStore()
