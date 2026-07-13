"""Executable support-resource RAG skill for the lead chat harness."""

from pathlib import Path
from typing import Any

from app.agents.resource_guidance import ResourceGuidanceAgentLoop
from app.llm.factory import create_llm_client
from app.models import Intent
from app.privacy.redaction import redact_sensitive_identifiers
from app.services.support_resource_service import (
    SupportResourceService,
    support_resource_service,
)
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class SupportRagSkill:
    """Run bounded resource guidance from the lead chat harness."""

    descriptor = SkillDescriptor(
        name="support_resource_rag_skill",
        description=(
            "Runs a bounded read-only resource-guidance loop with citations, "
            "step limits, and deterministic fallback."
        ),
        supported_intents=(Intent.CAMPUS_RESOURCE_QUERY,),
        entrypoint="app.skills.support_rag.SupportRagSkill.run",
        safety_notes=(
            "Runs only after the lead harness safety gate; tools are read-only, "
            "and crisis is handled before this skill."
        ),
        manifest_path=str(Path(__file__).parent / "manifests" / "support_rag" / "SKILL.md"),
    )

    def __init__(
        self,
        service: SupportResourceService | None = None,
        agent_loop: ResourceGuidanceAgentLoop | None = None,
    ) -> None:
        self.service = service or support_resource_service
        self.agent_loop = agent_loop or ResourceGuidanceAgentLoop(
            llm_client=create_llm_client(),
            knowledge=self.service.knowledge,
            fallback_service=self.service,
            max_steps=3,
        )

    async def run(self, context: SkillContext) -> SkillResult:
        """Return grounded output from a bounded read-only agent loop."""
        result = await self.agent_loop.run(context.message)
        structured_data: dict[str, Any] = {
            "agent": "support_resource_rag_agent",
            "action": "support_resources_queried",
            "citations": [citation.model_dump(mode="json") for citation in result.citations],
            "unknown": result.unknown,
            "confidence": result.confidence,
            "next_ui": "support",
            "blocked": result.blocked,
            "agent_loop_used": result.used_agent_loop,
            "agent_loop_max_steps": self.agent_loop.max_steps,
            "agent_loop_stop_reason": result.stop_reason.value,
            "agent_loop_fallback_used": result.fallback_used,
            "agent_loop_steps": [_trace_safe_step(step) for step in result.steps],
            "agent_loop_llm_usage": result.llm_usage.model_dump(mode="json"),
            "grounding_metadata": {
                "retrieval_unknown": result.unknown,
                "citation_count": len(result.citations),
                "citation_titles": [citation.title for citation in result.citations[:10]],
                "resource_contact_verified": False,
            },
        }
        return SkillResult(
            response=result.answer,
            structured_data=structured_data,
            selected_agent="support_resource_rag_agent",
        )


def _trace_safe_step(step: object) -> dict[str, Any]:
    """Redact model-provided text before exposing loop step metadata."""
    from app.models_resource_loop import ResourceLoopStep

    if not isinstance(step, ResourceLoopStep):
        return {}
    payload = step.model_dump(mode="json")
    for field in ("reason", "query"):
        value = payload.get(field)
        if isinstance(value, str):
            payload[field] = redact_sensitive_identifiers(value)[0]
    return payload
