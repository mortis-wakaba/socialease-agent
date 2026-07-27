"""Legacy harness boundary for exposure requests."""

from pathlib import Path

from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.skills.unified_conversation import unified_conversation_result


class ExposurePlanningSkill:
    """Redirect legacy requests without creating an orphan exposure plan."""

    descriptor = SkillDescriptor(
        name="exposure_planning_skill",
        description="Directs exposure requests to the unified conversation workflow.",
        supported_intents=(Intent.EXPOSURE_PLANNING, Intent.PROGRESS_REVIEW),
        entrypoint="app.skills.exposure.ExposurePlanningSkill.run",
        safety_notes="Never starts a module outside the unified conversation timeline.",
        manifest_path=str(Path(__file__).parent / "manifests" / "exposure" / "SKILL.md"),
    )

    async def run(self, context: SkillContext) -> SkillResult:
        """Return a migration-safe result after legacy consent."""
        del context
        return unified_conversation_result("分级练习")
