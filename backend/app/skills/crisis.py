"""Crisis escalation skill for high-risk inputs."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class CrisisEscalationSkill:
    """Safety-first crisis escalation skill that bypasses normal agents."""

    descriptor = SkillDescriptor(
        name="crisis_escalation_skill",
        description="Escalates crisis-risk messages to real-world support instead of normal agent behavior.",
        supported_intents=(Intent.CRISIS,),
        entrypoint="app.skills.crisis.CrisisEscalationSkill.run",
        safety_notes="Must be selected whenever safety risk is crisis; does not diagnose or provide treatment.",
        manifest_path=str(Path(__file__).parent / "manifests" / "crisis" / "SKILL.md"),
    )

    def run(self, context: SkillContext) -> SkillResult:
        """Return a non-medical crisis escalation response."""
        response, structured_data = self._crisis_escalation_response()
        return SkillResult(
            response=response,
            structured_data=structured_data,
            selected_agent="crisis_escalation",
        )

    @staticmethod
    def _crisis_escalation_response() -> tuple[str, dict[str, Any]]:
        response = (
            "我很担心你现在的安全。这个系统不能处理危机，也不能替代专业帮助。\n\n"
            "如果你可能马上伤害自己或他人，请立刻联系当地紧急服务，或请身边可信任的人陪你一起求助。"
            "如果你在学校，也建议尽快联系学校心理中心、辅导员或宿舍管理人员。\n\n"
            "在获得现实帮助前，尽量不要独处，远离可能伤害自己或他人的物品，并把这条信息直接发给一个"
            "你信任的人：我现在不安全，需要你马上陪我联系帮助。"
        )
        structured_data = {
            "agent": "crisis_escalation",
            "escalation": True,
            "recommended_actions": [
                "contact_local_emergency_services",
                "contact_trusted_person_now",
                "contact_school_counseling_center_or_counselor",
                "avoid_being_alone_until_help_arrives",
            ],
        }
        return response, structured_data
