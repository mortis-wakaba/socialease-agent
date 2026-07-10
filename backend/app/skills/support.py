"""Executable support skill adapter around the existing SupportAgent."""

from pathlib import Path

from app.agents.support import SupportAgent
from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class SupportSkill:
    """General non-medical support skill used by the chat harness."""

    descriptor = SkillDescriptor(
        name="general_support_skill",
        description="Provides non-medical emotional support and routes users toward practice/resource options.",
        supported_intents=(
            Intent.EMOTIONAL_SUPPORT,
            Intent.ROLEPLAY_PRACTICE,
            Intent.CBT_WORKSHEET,
            Intent.EXPOSURE_PLANNING,
            Intent.CAMPUS_RESOURCE_QUERY,
            Intent.PROGRESS_REVIEW,
        ),
        entrypoint="app.agents.support.SupportAgent.respond",
        safety_notes="Only runs after safety classification; crisis inputs are handled by crisis_escalation_skill.",
        manifest_path=str(Path(__file__).parent / "manifests" / "general_support" / "SKILL.md"),
    )

    def __init__(self, support_agent: SupportAgent | None = None) -> None:
        self.support_agent = support_agent or SupportAgent()

    async def run(self, context: SkillContext) -> SkillResult:
        """Run the existing support agent through the skill interface."""
        response, structured_data = self.support_agent.respond(
            message=context.message,
            intent=context.intent,
            safety_result=context.safety_result,
        )
        return SkillResult(
            response=response,
            structured_data=structured_data,
            selected_agent="support_agent",
        )
