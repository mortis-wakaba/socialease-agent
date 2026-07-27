"""Skill interfaces for SocialEase's safety-aware agent harness."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models import Intent, SafetyResult
from app.models_active_memory import ActiveMemoryPacket
from app.models_conversation_context import ConversationPromptContext
from app.models_context import SkillContextProjection
from app.models_support_generation import PresentationConstraints
from app.workflow.context import RunContext


@dataclass(frozen=True)
class SkillContext:
    """Runtime context passed from the harness into a skill."""

    run: RunContext

    @property
    def user_id(self) -> str:
        """Return the current user id."""
        return self.run.user_id

    @property
    def message(self) -> str:
        """Return the current user message."""
        return self.run.message

    @property
    def intent(self) -> Intent:
        """Return the routed intent for this skill run."""
        if self.run.intent_result is None:
            raise RuntimeError("SkillContext.intent accessed before routing.")
        return self.run.intent_result.intent

    @property
    def safety_result(self) -> SafetyResult:
        """Return the safety result for this skill run."""
        if self.run.safety_result is None:
            raise RuntimeError("SkillContext.safety_result accessed before safety.")
        return self.run.safety_result

    @property
    def request_context(self) -> dict[str, Any]:
        """Return caller-provided context slots."""
        return self.run.request_context

    @property
    def conversation_context(self) -> ConversationPromptContext | None:
        """Return trusted, bounded history selected by the application."""
        return self.run.conversation_context

    @property
    def selected_context(self) -> SkillContextProjection | None:
        """Return the task-specific context projection selected by the harness."""
        return self.run.skill_context

    @property
    def active_memory(self) -> ActiveMemoryPacket | None:
        """Return the fully policy- and budget-filtered active memory packet."""
        return self.run.active_memory

    @property
    def response_constraints(self) -> PresentationConstraints:
        """Return explicit presentation preferences independent from business intent."""
        return self.run.response_constraints


@dataclass(frozen=True)
class SkillResult:
    """Normalized result returned by a skill to the harness."""

    response: str
    structured_data: dict[str, Any] = field(default_factory=dict)
    selected_agent: str = "unknown_skill"


@dataclass(frozen=True)
class SkillDescriptor:
    """Human-readable metadata for a registered skill."""

    name: str
    description: str
    supported_intents: tuple[Intent, ...]
    entrypoint: str
    safety_notes: str
    manifest_path: str | None = None


class BaseSkill(Protocol):
    """Minimal executable skill contract used by the agent harness."""

    descriptor: SkillDescriptor

    async def run(self, context: SkillContext) -> SkillResult:
        """Execute this skill for one harness context."""
        ...
