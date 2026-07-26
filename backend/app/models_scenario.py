"""Open-world social-practice scenario contracts."""

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SocialSkillCode(str, Enum):
    """Stable social skills that can transfer across concrete situations."""

    CONVERSATION_INITIATION = "conversation_initiation"
    QUESTION_ASKING = "question_asking"
    ASSERTIVE_EXPRESSION = "assertive_expression"
    BOUNDARY_SETTING = "boundary_setting"
    SPECIFIC_REQUEST = "specific_request"
    DISAGREEMENT = "disagreement"
    EMPATHY = "empathy"
    CONFLICT_REPAIR = "conflict_repair"
    INVITATION = "invitation"
    SELF_INTRODUCTION = "self_introduction"
    CONVERSATION_EXIT = "conversation_exit"
    COLLABORATIVE_PROBLEM_SOLVING = "collaborative_problem_solving"


class ScenarioCounterpartRole(str, Enum):
    """Broad counterpart roles without retaining a person's identity."""

    PEER = "peer"
    AUTHORITY = "authority"
    GROUP = "group"
    INTERVIEWER = "interviewer"
    ACQUAINTANCE = "acquaintance"
    UNSPECIFIED = "unspecified"


class ScenarioInteractionMode(str, Enum):
    """Reusable interaction shapes for role-play planning."""

    START_CONVERSATION = "start_conversation"
    EXPRESS_VIEW = "express_view"
    ASK_QUESTION = "ask_question"
    MAKE_REQUEST = "make_request"
    RESPOND_TO_REQUEST = "respond_to_request"
    HANDLE_CONFLICT = "handle_conflict"
    INVITE = "invite"
    INTRODUCE_SELF = "introduce_self"
    UNSPECIFIED = "unspecified"


class ScenarioSpec(BaseModel):
    """Privacy-minimized structure for one concrete practice situation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scenario_id: str = Field(
        default_factory=lambda: f"scenario_{uuid4().hex}",
        min_length=1,
        max_length=128,
    )
    safe_summary: str = Field(min_length=1, max_length=240)
    practice_goal: str = Field(min_length=1, max_length=200)
    counterpart_role: ScenarioCounterpartRole = (
        ScenarioCounterpartRole.UNSPECIFIED
    )
    interaction_mode: ScenarioInteractionMode = (
        ScenarioInteractionMode.UNSPECIFIED
    )
    skill_codes: list[SocialSkillCode] = Field(min_length=1, max_length=5)
    context_tags: list[str] = Field(default_factory=list, max_length=5)

