"""Skill registry used to describe and dispatch SocialEase agent capabilities."""

from collections.abc import Iterable

from app.models import Intent, RiskLevel, SafetyResult
from app.skills.base import BaseSkill, SkillDescriptor
from app.skills.crisis import CrisisEscalationSkill
from app.skills.exposure import ExposurePlanningSkill
from app.skills.roleplay import RoleplaySkill
from app.skills.support import SupportSkill
from app.skills.support_rag import SupportRagSkill
from app.skills.worksheet import WorksheetSkill


class SkillRegistry:
    """Registry for executable harness skills and documented specialized skills."""

    def __init__(self, executable_skills: Iterable[BaseSkill] | None = None) -> None:
        skills = tuple(
            executable_skills
            or (
                CrisisEscalationSkill(),
                SupportSkill(),
                RoleplaySkill(),
                WorksheetSkill(),
                ExposurePlanningSkill(),
                SupportRagSkill(),
            )
        )
        self._executable_by_name = {skill.descriptor.name: skill for skill in skills}
        self._descriptors_by_name: dict[str, SkillDescriptor] = {
            skill.descriptor.name: skill.descriptor for skill in skills
        }

    def list_descriptors(self) -> list[SkillDescriptor]:
        """Return metadata for all skills known to the project."""
        return sorted(self._descriptors_by_name.values(), key=lambda item: item.name)

    def resolve_for_chat(self, intent: Intent, safety_result: SafetyResult) -> BaseSkill:
        """Resolve the executable skill used by the chat harness."""
        if safety_result.risk_level == RiskLevel.CRISIS or intent == Intent.CRISIS:
            return self._executable_by_name["crisis_escalation_skill"]
        skill_name_by_intent = {
            Intent.EMOTIONAL_SUPPORT: "general_support_skill",
            Intent.ROLEPLAY_PRACTICE: "roleplay_skill",
            Intent.CBT_WORKSHEET: "worksheet_skill",
            Intent.EXPOSURE_PLANNING: "exposure_planning_skill",
            Intent.PROGRESS_REVIEW: "exposure_planning_skill",
            Intent.CAMPUS_RESOURCE_QUERY: "support_resource_rag_skill",
        }
        skill_name = skill_name_by_intent.get(intent, "general_support_skill")
        return self._executable_by_name.get(
            skill_name,
            self._executable_by_name["general_support_skill"],
        )

    def descriptor_for_intent(self, intent: Intent) -> SkillDescriptor | None:
        """Return the most specific descriptor that declares support for an intent."""
        fallback: SkillDescriptor | None = None
        for descriptor in self.list_descriptors():
            if intent not in descriptor.supported_intents:
                continue
            if descriptor.name == "general_support_skill":
                fallback = descriptor
                continue
            return descriptor
        return fallback


skill_registry = SkillRegistry()
