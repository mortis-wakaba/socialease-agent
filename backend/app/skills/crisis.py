"""Crisis escalation skill for high-risk inputs."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.safety.crisis import full_crisis_escalation_response
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

    async def run(self, context: SkillContext) -> SkillResult:
        """Return a non-medical crisis escalation response."""
        response, structured_data = self._crisis_escalation_response()
        return SkillResult(
            response=response,
            structured_data=structured_data,
            selected_agent="crisis_escalation",
        )

    @staticmethod
    def _crisis_escalation_response() -> tuple[str, dict[str, Any]]:
        response = full_crisis_escalation_response()
        structured_data = {
            "agent": "crisis_escalation",
            "action": "crisis_escalation",
            "escalation": True,
            "recommended_actions": [
                "contact_local_emergency_services",
                "contact_trusted_person_now",
                "contact_school_counseling_center_or_counselor",
                "avoid_being_alone_until_help_arrives",
            ],
        }
        return response, structured_data
