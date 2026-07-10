"""Skill interfaces for SocialEase's safety-aware agent harness."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models import Intent, SafetyResult
from app.models_memory import MemoryContext
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
    def memory_context(self) -> MemoryContext | None:
        """Return privacy-safe memory context for this run."""
        return self.run.memory_context


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
