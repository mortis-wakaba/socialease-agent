"""Pydantic models for session-level intervention plans."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


StepStatus = Literal["pending", "in_progress", "completed", "skipped", "cancelled", "blocked"]
PlanStatus = Literal["pending_consent", "active", "completed", "cancelled", "blocked", "paused"]


class InterventionStep(BaseModel):
    """One bounded step in a session-level intervention plan."""

    step_id: str
    title: str
    status: StepStatus = "pending"
    skill: str
    intensity: int | None = None
    requires_consent: bool = False
    protocol_id: str | None = None
    stop_condition: str | None = None
    result_summary: str | None = None


class InterventionPlan(BaseModel):
    """Persisted intervention plan for one chat/session flow."""

    plan_id: str
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    status: PlanStatus = "active"
    protocol_id: str | None = None
    steps: list[InterventionStep] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class InterventionStepView(BaseModel):
    """Display-friendly step state for intervention plan timelines."""

    order: int
    step_id: str
    title: str
    status: StepStatus
    skill: str
    intensity: int | None = None
    requires_consent: bool = False
    protocol_id: str | None = None
    stop_condition: str | None = None
    result_summary: str | None = None
    is_current: bool = False


class InterventionPlanView(BaseModel):
    """Traceable intervention plan view for API and frontend display."""

    plan_id: str
    user_id: str
    session_id: str
    status: PlanStatus
    protocol_id: str | None = None
    current_step_id: str | None = None
    completed_steps: int = 0
    total_steps: int = 0
    progress_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    timeline: list[InterventionStepView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class InterventionPlanResponse(BaseModel):
    """Response for one intervention plan."""

    plan: InterventionPlanView


class InterventionPlanListResponse(BaseModel):
    """Response for a user's recent intervention plans."""

    user_id: str
    plans: list[InterventionPlanView] = Field(default_factory=list)
