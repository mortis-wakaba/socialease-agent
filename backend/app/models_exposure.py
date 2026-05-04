"""Pydantic models for graded exposure practice planning."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models import SafetyResult
from app.models_knowledge import Citation


EXPOSURE_DISCLAIMER = (
    "这是社交练习的分级计划，仅用于安排安全、可控的小步骤；它不用于判断疾病，"
    "也不能替代专业心理支持。请只选择你可以随时暂停的练习；如果你担心安全或压力明显影响日常生活，请联系可信任的人、"
    "学校心理中心或当地紧急服务。"
)


class ExposureFeedbackStatus(str, Enum):
    """Supported completion feedback statuses."""

    COMPLETED = "completed"
    SKIPPED = "skipped"
    TOO_HARD = "too_hard"


class ExposureTask(BaseModel):
    """One task in an exposure practice ladder."""

    task_id: str
    title: str
    description: str
    difficulty: int = Field(ge=1, le=10)
    estimated_time_minutes: int = Field(ge=1)
    success_criteria: str
    fallback_task: str
    citations: list[Citation] = Field(default_factory=list)


class ExposureAttempt(BaseModel):
    """Recorded feedback for one exposure task attempt."""

    task_id: str
    status: ExposureFeedbackStatus
    anxiety_before: int = Field(ge=1, le=10)
    anxiety_after: int = Field(ge=1, le=10)
    reflection: str
    created_at: datetime


class ExposurePlan(BaseModel):
    """Persisted exposure plan for a user."""

    plan_id: str
    user_id: str = Field(min_length=1)
    target_scenario: str = Field(min_length=1)
    current_anxiety_level: int = Field(ge=1, le=10)
    previous_attempts: list[str] = Field(default_factory=list)
    tasks: list[ExposureTask]
    attempts: list[ExposureAttempt] = Field(default_factory=list)
    recommended_next_task_id: str | None = None
    disclaimer: str = EXPOSURE_DISCLAIMER
    created_at: datetime
    updated_at: datetime


class ExposurePlanRequest(BaseModel):
    """Request body for creating an exposure plan."""

    user_id: str = Field(min_length=1)
    target_scenario: str = Field(min_length=1)
    current_anxiety_level: int = Field(ge=1, le=10)
    previous_attempts: list[str] = Field(default_factory=list)


class ExposurePlanResponse(BaseModel):
    """Response returned after creating an exposure plan."""

    plan: ExposurePlan | None = None
    safety_result: SafetyResult
    blocked: bool = False
    response: str


class ExposureCompleteRequest(BaseModel):
    """Request body for recording exposure task feedback."""

    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    status: ExposureFeedbackStatus
    anxiety_before: int = Field(ge=1, le=10)
    anxiety_after: int = Field(ge=1, le=10)
    reflection: str = Field(default="")


class ExposureCompleteResponse(BaseModel):
    """Response returned after updating exposure progress."""

    plan: ExposurePlan
    next_task: ExposureTask | None
    adjustment_reason: str


class UserExposureResponse(BaseModel):
    """Response for a user's exposure progress."""

    user_id: str
    plan: ExposurePlan | None = None
