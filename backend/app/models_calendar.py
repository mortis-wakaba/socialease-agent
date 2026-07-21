"""Structured models for bounded calendar proposals and MCP tool results."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class CalendarRecurrence(str, Enum):
    """Recurrence rules supported by the first bounded calendar version."""

    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"


class CalendarEventStatus(str, Enum):
    """Lifecycle states for SocialEase-owned calendar events."""

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class CalendarEventProposal(BaseModel):
    """User-reviewable proposal produced before any external write occurs."""

    title: str = Field(min_length=1, max_length=80)
    start_time: datetime
    duration_minutes: int = Field(ge=5, le=60)
    recurrence: CalendarRecurrence = CalendarRecurrence.NONE
    recurrence_end_date: date | None = None
    reminder_minutes: int = Field(default=10, ge=0, le=60)

    @model_validator(mode="after")
    def validate_bounded_schedule(self) -> "CalendarEventProposal":
        """Require timezone-aware, finite and non-medical reminder proposals."""
        if self.start_time.tzinfo is None or self.start_time.utcoffset() is None:
            raise ValueError("start_time must include an explicit timezone")
        lowered = self.title.casefold()
        forbidden = ("诊断", "治疗", "治愈", "服药", "焦虑症", "depression", "diagnosis")
        if any(term in lowered for term in forbidden):
            raise ValueError("calendar title must remain neutral and non-medical")
        if self.recurrence == CalendarRecurrence.NONE:
            if self.recurrence_end_date is not None:
                raise ValueError("non-recurring events cannot set recurrence_end_date")
            return self
        if self.recurrence_end_date is None:
            raise ValueError("recurring events require recurrence_end_date")
        start_date = self.start_time.date()
        if self.recurrence_end_date < start_date:
            raise ValueError("recurrence_end_date cannot precede start_time")
        if self.recurrence_end_date > start_date + timedelta(days=30):
            raise ValueError("recurring events cannot extend beyond 30 days")
        return self


class CalendarEventRecord(BaseModel):
    """Owner-scoped event identity returned by a Calendar Provider."""

    calendar_action_id: str
    provider_event_id: str
    user_id: str = Field(min_length=1)
    proposal: CalendarEventProposal
    idempotency_key: str = Field(min_length=8, max_length=120)
    status: CalendarEventStatus = CalendarEventStatus.CONFIRMED
    created_at: datetime
    updated_at: datetime
    idempotency_reused: bool = False


class CalendarToolTrace(BaseModel):
    """Privacy-minimized diagnostics for one calendar tool workflow."""

    tool_name: str
    transport: str
    status: str
    calendar_action_id: str | None = None
    latency_ms: float = Field(ge=0.0)
    idempotency_reused: bool = False
    read_after_write_verified: bool = False
    error_category: str | None = None


class CalendarCreateRequest(BaseModel):
    """Direct API request for one user-approved calendar creation."""

    user_id: str = Field(min_length=1)
    proposal: CalendarEventProposal
    idempotency_key: str = Field(min_length=8, max_length=120)


class CalendarUpdateRequest(BaseModel):
    """Direct API request to replace one owned event proposal."""

    user_id: str = Field(min_length=1)
    proposal: CalendarEventProposal


class CalendarDeleteRequest(BaseModel):
    """Direct API request to cancel one owned event."""

    user_id: str = Field(min_length=1)


class CalendarEventResponse(BaseModel):
    """Public response containing an owned event and low-sensitivity tool trace."""

    event: CalendarEventRecord
    verified: bool = False
    tool_trace: CalendarToolTrace


class CalendarEventListResponse(BaseModel):
    """Public response for SocialEase-owned events only."""

    events: list[CalendarEventRecord] = Field(default_factory=list)

