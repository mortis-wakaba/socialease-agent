"""Executable adapter for grounded CBT-style support generation."""

from pathlib import Path

from app.agents.support import SupportAgent
from app.agents.support_generation import SupportGenerationAgent
from app.llm.factory import create_llm_client
from app.models import Intent
from app.models_context import SupportGenerationContext
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
        entrypoint="app.agents.support_generation.SupportGenerationAgent.respond",
        safety_notes=(
            "Only runs after safety classification; output is schema validated and guarded, "
            "with deterministic fallback. Crisis inputs are handled before this skill."
        ),
        manifest_path=str(Path(__file__).parent / "manifests" / "general_support" / "SKILL.md"),
    )

    def __init__(
        self,
        support_agent: SupportAgent | None = None,
        generation_agent: SupportGenerationAgent | None = None,
    ) -> None:
        self.support_agent = support_agent or SupportAgent()
        self.generation_agent = generation_agent or SupportGenerationAgent(
            llm_client=create_llm_client(),
            fallback_agent=self.support_agent,
        )

    async def run(self, context: SkillContext) -> SkillResult:
        """Run grounded generation and fall back safely on provider or guardrail errors."""
        support_context = None
        if (
            context.selected_context is not None
            and context.selected_context.skill_name == self.descriptor.name
        ):
            support_context = SupportGenerationContext.model_validate(
                context.selected_context.values
            )
        response, structured_data = await self.generation_agent.respond(
            message=context.message,
            intent=context.intent,
            safety_result=context.safety_result,
            support_context=support_context,
            conversation_context=context.conversation_context,
            application_constraints=context.response_constraints,
        )
        return SkillResult(
            response=response,
            structured_data=structured_data,
            selected_agent=str(structured_data.get("agent", "support_agent")),
        )
