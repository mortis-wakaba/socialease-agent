"""Pydantic models for role-play practice sessions."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models import SafetyResult
from app.models_knowledge import Citation


class RoleplayScenario(str, Enum):
    """Supported social role-play scenarios."""

    CLASSROOM_SPEECH = "classroom_speech"
    GROUP_DISCUSSION = "group_discussion"
    DORM_CONFLICT = "dorm_conflict"
    CLUB_ICEBREAKING = "club_icebreaking"
    INVITE_CLASSMATE_MEAL = "invite_classmate_meal"
    ASK_TEACHER_QUESTION = "ask_teacher_question"
    INTERVIEW_SELF_INTRO = "interview_self_intro"
    REFUSE_REQUEST = "refuse_request"
    EXPRESS_DISAGREEMENT = "express_disagreement"


class RoleplayMessageRole(str, Enum):
    """Message roles within a role-play session."""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class RoleplayMessage(BaseModel):
    """One message in a role-play session."""

    role: RoleplayMessageRole
    content: str
    created_at: datetime


class RoleplayGuidance(BaseModel):
    """Retrieved social-skills guidance used to ground a role-play session."""

    query: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)
    no_guidance_found: bool = False


class RoleplaySession(BaseModel):
    """Persisted role-play session state."""

    session_id: str
    user_id: str = Field(min_length=1)
    scenario: RoleplayScenario
    difficulty: int = Field(ge=1, le=5)
    messages: list[RoleplayMessage] = Field(default_factory=list)
    retrieved_guidance: RoleplayGuidance
    created_at: datetime
    updated_at: datetime


class RoleplayStartRequest(BaseModel):
    """Request body for starting a role-play session."""

    user_id: str = Field(min_length=1)
    scenario: RoleplayScenario
    difficulty: int = Field(default=2, ge=1, le=5)


class RoleplayStartResponse(BaseModel):
    """Response returned after creating a role-play session."""

    session: RoleplaySession
    opening_message: str


class RoleplayMessageRequest(BaseModel):
    """Request body for sending a role-play message."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class RoleplayMessageResponse(BaseModel):
    """Response returned after one role-play turn."""

    session: RoleplaySession
    response: str
    safety_result: SafetyResult
    blocked: bool = False


class RoleplayFeedbackRequest(BaseModel):
    """Request body for role-play feedback."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class RoleplayFeedback(BaseModel):
    """Structured feedback for a role-play session."""

    clarity_score: int = Field(ge=1, le=5)
    naturalness_score: int = Field(ge=1, le=5)
    assertiveness_score: int = Field(ge=1, le=5)
    empathy_score: int = Field(ge=1, le=5)
    strengths: list[str]
    suggestions: list[str]
    next_try_prompt: str
    citations: list[Citation] = Field(default_factory=list)


class RoleplayFeedbackResponse(BaseModel):
    """Response returned by the role-play feedback endpoint."""

    session: RoleplaySession
    feedback: RoleplayFeedback
