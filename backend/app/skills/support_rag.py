"""Legacy harness boundary for support-resource requests."""

from pathlib import Path

from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.skills.unified_conversation import unified_conversation_result


class SupportRagSkill:
    """Redirect legacy requests without running a detached resource module."""

    descriptor = SkillDescriptor(
        name="support_resource_rag_skill",
        description=(
            "Directs support-resource requests to the unified conversation workflow."
        ),
        supported_intents=(Intent.CAMPUS_RESOURCE_QUERY,),
        entrypoint="app.skills.support_rag.SupportRagSkill.run",
        safety_notes=(
            "Never starts a module outside the unified conversation timeline."
        ),
        manifest_path=str(Path(__file__).parent / "manifests" / "support_rag" / "SKILL.md"),
    )

    async def run(self, context: SkillContext) -> SkillResult:
        """Return a migration-safe result after legacy routing."""
        del context
        return unified_conversation_result("支持资源导航")
