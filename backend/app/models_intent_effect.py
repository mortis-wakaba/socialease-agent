"""Durable ledger models for reversible conversation side effects."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class IntentEffectLedgerStatus(str, Enum):
    """Lifecycle synchronized between intent plans and action outboxes."""

    APPLY_PENDING = "apply_pending"
    APPLIED = "applied"
    UNDO_PENDING = "undo_pending"
    UNDONE = "undone"


class IntentEffectLedgerRecord(BaseModel):
    """Owner-scoped effect receipt retained independently from current plans."""

    model_config = ConfigDict(extra="forbid")

    effect_id: str = Field(min_length=1, max_length=64)
    plan_id: str = Field(min_length=1, max_length=64)
    step_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(pattern="^calendar_event$")
    status: IntentEffectLedgerStatus
    create_job_id: str = Field(min_length=1, max_length=64)
    undo_job_id: str | None = Field(default=None, min_length=1, max_length=64)
    external_action_id: str | None = Field(default=None, min_length=1, max_length=200)
    create_idempotency_key: str = Field(min_length=8, max_length=200)
    undo_idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
