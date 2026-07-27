"""Legacy harness boundary for worksheet requests."""

from pathlib import Path

from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.skills.unified_conversation import unified_conversation_result


class WorksheetSkill:
    """Redirect legacy requests without creating an orphan worksheet."""

    descriptor = SkillDescriptor(
        name="worksheet_skill",
        description="Directs worksheet requests to the unified conversation workflow.",
        supported_intents=(Intent.CBT_WORKSHEET,),
        entrypoint="app.skills.worksheet.WorksheetSkill.run",
        safety_notes="Never starts a module outside the unified conversation timeline.",
        manifest_path=str(Path(__file__).parent / "manifests" / "worksheet" / "SKILL.md"),
    )

    async def run(self, context: SkillContext) -> SkillResult:
        """Return a migration-safe result after legacy routing."""
        del context
        return unified_conversation_result("结构化自助练习")
