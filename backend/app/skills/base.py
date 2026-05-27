"""Skill interfaces for SocialEase's safety-aware agent harness."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models import Intent, SafetyResult


@dataclass(frozen=True)
class SkillContext:
    """Runtime context passed from the harness into a skill."""

    user_id: str
    message: str
    intent: Intent
    safety_result: SafetyResult
    request_context: dict[str, Any] = field(default_factory=dict)


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

    def run(self, context: SkillContext) -> SkillResult:
        """Execute this skill for one harness context."""
        ...
