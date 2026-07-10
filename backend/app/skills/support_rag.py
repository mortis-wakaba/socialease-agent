"""Executable support-resource RAG skill for the lead chat harness."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.models_support import SupportQueryRequest
from app.services.support_resource_service import (
    SupportResourceService,
    support_resource_service,
)
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class SupportRagSkill:
    """Query grounded support resources from the lead chat harness."""

    descriptor = SkillDescriptor(
        name="support_resource_rag_skill",
        description="Queries grounded public support resources with citations and unknown handling.",
        supported_intents=(Intent.CAMPUS_RESOURCE_QUERY,),
        entrypoint="app.skills.support_rag.SupportRagSkill.run",
        safety_notes="Runs only after the lead harness safety gate; crisis is handled before this skill.",
        manifest_path=str(Path(__file__).parent / "manifests" / "support_rag" / "SKILL.md"),
    )

    def __init__(self, service: SupportResourceService | None = None) -> None:
        self.service = service or support_resource_service

    async def run(self, context: SkillContext) -> SkillResult:
        """Return grounded support-resource guidance for a chat query."""
        result = await self.service.query_resources(SupportQueryRequest(query=context.message))
        structured_data: dict[str, Any] = {
            "agent": "support_resource_rag_agent",
            "action": "support_resources_queried",
            "citations": [citation.model_dump(mode="json") for citation in result.citations],
            "unknown": result.unknown,
            "confidence": result.confidence,
            "next_ui": "support",
            "blocked": result.blocked,
        }
        return SkillResult(
            response=result.answer,
            structured_data=structured_data,
            selected_agent="support_resource_rag_agent",
        )
