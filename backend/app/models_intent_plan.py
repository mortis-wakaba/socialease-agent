"""Persistent, owner-scoped state for sequential multi-intent plans."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models import IntentExecutionPlan


class ConversationIntentPlanStatus(str, Enum):
    """Aggregate lifecycle for one conversation's current intent plan."""

    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ConversationIntentPlan(BaseModel):
    """Durable sequential intent queue created from one user message."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    source_event_id: str = Field(min_length=1, max_length=64)
    execution_plan: IntentExecutionPlan
    status: ConversationIntentPlanStatus = ConversationIntentPlanStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
