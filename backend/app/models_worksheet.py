"""Pydantic models for CBT-style self-reflection worksheets."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models import SafetyResult
from app.models_knowledge import Citation


WORKSHEET_DISCLAIMER = (
    "这是一个 CBT 风格的自助反思练习，仅用于整理想法和下一步行动；"
    "它不用于判断疾病，也不能替代专业心理支持。"
    "如果你担心自己的安全，或压力已经明显影响日常生活，请联系可信任的人、学校心理中心或当地紧急服务。"
)


class WorksheetFields(BaseModel):
    """Structured fields for a CBT-style worksheet."""

    situation: str | None = None
    automatic_thought: str | None = None
    emotion: str | None = None
    emotion_intensity: int | None = Field(default=None, ge=0, le=10)
    evidence_for: str | None = None
    evidence_against: str | None = None
    alternative_thought: str | None = None
    next_action: str | None = None


class WorksheetRecord(BaseModel):
    """Persisted worksheet record."""

    worksheet_id: str
    user_id: str = Field(min_length=1)
    source_message: str
    fields: WorksheetFields
    citations: list[Citation] = Field(default_factory=list)
    disclaimer: str
    missing_fields: list[str] = Field(default_factory=list)
    gentle_followup_questions: list[str] = Field(default_factory=list)
    created_at: datetime


class WorksheetCreateRequest(BaseModel):
    """Request body for creating a CBT-style worksheet."""

    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class WorksheetCreateResponse(BaseModel):
    """Response returned by worksheet creation."""

    worksheet: WorksheetRecord | None = None
    safety_result: SafetyResult
    missing_fields: list[str] = Field(default_factory=list)
    gentle_followup_questions: list[str] = Field(default_factory=list)
    disclaimer: str = WORKSHEET_DISCLAIMER
    blocked: bool = False
    response: str
