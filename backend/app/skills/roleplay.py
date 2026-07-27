"""Legacy harness boundary for role-play requests."""

from pathlib import Path

from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


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
        return SkillResult(
            response=(
                "请在统一对话中继续；系统会先给出角色扮演选项，"
                "只有你确认后才会在当前对话里开始练习。"
            ),
            structured_data={
                "agent": "lead_harness",
                "action": "use_unified_conversation",
                "next_ui": "chat",
                "consent_required": True,
                "deprecated_entrypoint": True,
                "blocked": False,
            },
            selected_agent="lead_harness",
        )
