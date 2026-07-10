"""Pydantic models for privacy-safe practice session reviews."""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


SessionReviewCompletion = Literal["completed", "partial", "pause"]
SessionReviewSource = Literal["roleplay", "worksheet", "exposure", "general"]


class SessionReviewCreateRequest(BaseModel):
    """Request to save a short structured practice review."""

    source: SessionReviewSource = "general"
    source_id: str | None = None
    completed: SessionReviewCompletion
    anxiety_before: int = Field(ge=1, le=10)
    anxiety_after: int = Field(ge=1, le=10)
    next_step: str = Field(min_length=1, max_length=240)
    save_record: bool = True


class SessionReviewRecord(BaseModel):
    """Persisted low-sensitivity session review record."""

    review_id: str = Field(default_factory=lambda: f"review_{uuid4().hex}")
    user_id: str
    source: SessionReviewSource = "general"
    source_id: str | None = None
    completed: SessionReviewCompletion
    anxiety_before: int = Field(ge=1, le=10)
    anxiety_after: int = Field(ge=1, le=10)
    next_step_summary: str = Field(max_length=240)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionReviewCreateResponse(BaseModel):
    """Response returned after saving a session review."""

    review: SessionReviewRecord | None
    saved: bool
    message: str


class SessionReviewListResponse(BaseModel):
    """Recent privacy-safe reviews for one user."""

    user_id: str
    reviews: list[SessionReviewRecord] = Field(default_factory=list)
