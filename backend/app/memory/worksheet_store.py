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
            updated_at=datetime.now(timezone.utc),
            completed=not missing_fields,
        )
        return self.repository.save(record)

    def get(self, worksheet_id: str) -> WorksheetRecord | None:
        """Return a worksheet by id, if present."""
        return self.repository.get(worksheet_id)

    def get_for_user(self, worksheet_id: str, user_id: str) -> WorksheetRecord | None:
        """Return a worksheet only when it belongs to the requesting user."""
        record = self.repository.get(worksheet_id)
        return record if record is not None and record.user_id == user_id else None

    def save(self, record: WorksheetRecord) -> WorksheetRecord:
        """Persist an updated privacy-minimized worksheet draft."""
        return self.repository.save(record)


worksheet_store = WorksheetStore()
