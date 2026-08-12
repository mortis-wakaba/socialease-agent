"""Executable adapter for grounded CBT-style support generation."""

from pathlib import Path

from app.agents.memory_evidence_selector import (
    MemoryEvidenceSelector,
    memory_evidence_selector_enabled,
)
from app.agents.memory_grounding import MemoryGroundingGenerationAgent
from app.agents.memory_tool import MemoryToolAgent
from app.agents.support import SupportAgent
from app.agents.support_generation import SupportGenerationAgent
from app.db.factory import repository_factory
from app.llm.factory import create_llm_client
from app.memory.retriever import EpisodicMemoryRetriever
from app.models import Intent, RiskLevel
from app.models_context import SupportGenerationContext
from app.services.memory_search_service import MemorySearchService
from app.services.memory_correction_service import (
    ConservativeMemoryUpdateService,
    MemoryCorrectionService,
)
from app.services.memory_center_service import memory_center_service
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.tools.practice_memory import memory_tool_registry


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
        memory_tool_agent: MemoryToolAgent | None = None,
        memory_grounding_agent: MemoryGroundingGenerationAgent | None = None,
    ) -> None:
        self.support_agent = support_agent or SupportAgent()
        if generation_agent is None:
            llm_client = create_llm_client()
            self.generation_agent = SupportGenerationAgent(
                llm_client=llm_client,
                fallback_agent=self.support_agent,
            )
            self.memory_grounding_agent = (
                memory_grounding_agent
                or MemoryGroundingGenerationAgent(llm_client=llm_client)
            )
            if memory_tool_agent is None:
                factory = repository_factory()
                settings = factory.user_memory_settings_repository()
                search_service = MemorySearchService(
                    EpisodicMemoryRetriever(
                        repository=factory.long_term_memory_repository(),
                        settings_repository=settings,
                    ),
                    evidence_selector=(
                        MemoryEvidenceSelector(llm_client=llm_client)
                        if memory_evidence_selector_enabled()
                        else None
                    ),
                )
                memory_tool_agent = MemoryToolAgent(
                    llm_client=llm_client,
                    tool_registry=memory_tool_registry(
                        search_service,
                        update_service=ConservativeMemoryUpdateService(
                            correction_service=MemoryCorrectionService(
                                memory_repository=(
                                    factory.long_term_memory_repository()
                                ),
                                settings_repository=settings,
                            ),
                            supersession_service=memory_center_service,
                        ),
                    ),
                )
        else:
            self.generation_agent = generation_agent
            self.memory_grounding_agent = memory_grounding_agent
        self.memory_tool_agent = memory_tool_agent

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
        memory_tool = (
            await self.memory_tool_agent.run(
                user_id=context.user_id,
                message=context.message,
                conversation_context=context.conversation_context,
                skill_name=self.descriptor.name,
                risk_level=context.safety_result.risk_level,
            )
            if self.memory_tool_agent is not None
            else None
        )
        memory_evidence = memory_tool.prompt_evidence if memory_tool else []
        if memory_tool is not None and memory_tool.outcome == "updated":
            response = "好的，我已经用你现在的说法替代了对应的旧记忆。"
            structured_data = {
                "agent": "memory_update_tool",
                "memory_updated": True,
            }
        elif memory_evidence and self.memory_grounding_agent is not None:
            response, structured_data = await self.memory_grounding_agent.respond(
                current_request=context.message,
                evidence=memory_evidence,
            )
        else:
            generation_kwargs = {
                "message": context.message,
                "intent": context.intent,
                "safety_result": context.safety_result,
                "support_context": support_context,
                "conversation_context": context.conversation_context,
                "memory_tool_evidence": memory_evidence,
                "application_constraints": context.response_constraints,
            }
            if (
                context.response_chunk_sink is not None
                and context.safety_result.risk_level is RiskLevel.LOW
                and context.intent
                in {Intent.EMOTIONAL_SUPPORT, Intent.PROGRESS_REVIEW}
                and not memory_evidence
            ):
                response, structured_data = (
                    await self.generation_agent.respond_stream(
                        **generation_kwargs,
                        chunk_sink=context.response_chunk_sink,
                    )
                )
            else:
                response, structured_data = await self.generation_agent.respond(
                    **generation_kwargs
                )
        if memory_tool is not None:
            structured_data["memory_tool"] = memory_tool.trace_metadata()
        return SkillResult(
            response=response,
            structured_data=structured_data,
            selected_agent=str(structured_data.get("agent", "support_agent")),
            memory_evidence=memory_evidence,
        )
