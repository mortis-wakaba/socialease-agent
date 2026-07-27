"""Legacy harness boundary for role-play requests."""

from pathlib import Path

from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.skills.unified_conversation import unified_conversation_result


class RoleplaySkill:
    """Redirect legacy requests without creating an orphan module session."""

    descriptor = SkillDescriptor(
        name="roleplay_skill",
        description="Directs role-play requests to the unified conversation workflow.",
        supported_intents=(Intent.ROLEPLAY_PRACTICE,),
        entrypoint="app.skills.roleplay.RoleplaySkill.run",
        safety_notes="Never starts a module outside the unified conversation timeline.",
        manifest_path=str(Path(__file__).parent / "manifests" / "roleplay" / "SKILL.md"),
    )

    async def run(self, context: SkillContext) -> SkillResult:
        """Return a migration-safe result after legacy consent."""
        del context
        return unified_conversation_result("角色扮演")
