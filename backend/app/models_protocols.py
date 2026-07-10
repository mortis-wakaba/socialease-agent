"""Pydantic models for consent and protocol handshakes."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProtocolStatus(str, Enum):
    """Supported protocol lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class ProtocolType(str, Enum):
    """Supported protocol request types."""

    CONSENT_REQUEST = "consent_request"


class ProtocolRecord(BaseModel):
    """Persisted protocol request/response state."""

    protocol_id: str
    user_id: str = Field(min_length=1)
    protocol_type: ProtocolType
    status: ProtocolStatus
    session_id: str | None = None
    harness_action: str | None = None
    request_hash: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    approved_at: datetime | None = None
    consumed_at: datetime | None = None
    rejected_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProtocolRespondRequest(BaseModel):
    """Request body for approving or rejecting a pending protocol."""

    user_id: str = Field(min_length=1)
    approved: bool


class ProtocolResponse(BaseModel):
    """API response containing one protocol record."""

    protocol: ProtocolRecord
