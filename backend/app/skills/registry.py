"""Skill registry used to describe and dispatch SocialEase agent capabilities."""

from collections.abc import Iterable
from pathlib import Path

from app.models import Intent, RiskLevel, SafetyResult
from app.skills.base import BaseSkill, SkillDescriptor
from app.skills.crisis import CrisisEscalationSkill
from app.skills.support import SupportSkill


MANIFEST_DIR = Path(__file__).parent / "manifests"


ROLEPLAY_SKILL_DESCRIPTOR = SkillDescriptor(
    name="roleplay_skill",
    description="Runs social scenario simulation and structured feedback through the role-play API.",
    supported_intents=(Intent.ROLEPLAY_PRACTICE,),
    entrypoint="app.api.roleplay / app.agents.roleplay.RoleplayAgent",
    safety_notes="Every role-play turn is safety-gated before normal practice continues.",
    manifest_path=str(MANIFEST_DIR / "roleplay" / "SKILL.md"),
)

WORKSHEET_SKILL_DESCRIPTOR = SkillDescriptor(
    name="worksheet_skill",
    description="Extracts CBT-style self-reflection worksheets with validated fields and fallback extraction.",
    supported_intents=(Intent.CBT_WORKSHEET,),
    entrypoint="app.api.worksheet / app.agents.worksheet.WorksheetAgent",
    safety_notes="Crisis inputs are blocked and do not create ordinary worksheets.",
    manifest_path=str(MANIFEST_DIR / "worksheet" / "SKILL.md"),
)

EXPOSURE_SKILL_DESCRIPTOR = SkillDescriptor(
    name="exposure_planning_skill",
    description="Builds graded, stoppable social practice ladders and records attempt feedback.",
    supported_intents=(Intent.EXPOSURE_PLANNING, Intent.PROGRESS_REVIEW),
    entrypoint="app.api.exposure / app.agents.exposure.ExposurePlanner",
    safety_notes="Plans are non-medical practice suggestions and are blocked for crisis inputs.",
    manifest_path=str(MANIFEST_DIR / "exposure" / "SKILL.md"),
)

SUPPORT_RAG_SKILL_DESCRIPTOR = SkillDescriptor(
    name="support_resource_rag_skill",
    description="Queries grounded public support resources with citations and unknown handling.",
    supported_intents=(Intent.CAMPUS_RESOURCE_QUERY,),
    entrypoint="app.api.support / app.knowledge.service.KnowledgeService",
    safety_notes="Only returns verified public resources; demo campus resources are not presented as real services.",
    manifest_path=str(MANIFEST_DIR / "support_rag" / "SKILL.md"),
)


class SkillRegistry:
    """Registry for executable harness skills and documented specialized skills."""

    def __init__(self, executable_skills: Iterable[BaseSkill] | None = None) -> None:
        skills = tuple(executable_skills or (CrisisEscalationSkill(), SupportSkill()))
        self._executable_by_name = {skill.descriptor.name: skill for skill in skills}
        self._descriptors_by_name: dict[str, SkillDescriptor] = {
            skill.descriptor.name: skill.descriptor for skill in skills
        }
        for descriptor in (
            ROLEPLAY_SKILL_DESCRIPTOR,
            WORKSHEET_SKILL_DESCRIPTOR,
            EXPOSURE_SKILL_DESCRIPTOR,
            SUPPORT_RAG_SKILL_DESCRIPTOR,
        ):
            self._descriptors_by_name.setdefault(descriptor.name, descriptor)

    def list_descriptors(self) -> list[SkillDescriptor]:
        """Return metadata for all skills known to the project."""
        return sorted(self._descriptors_by_name.values(), key=lambda item: item.name)

    def resolve_for_chat(self, intent: Intent, safety_result: SafetyResult) -> BaseSkill:
        """Resolve the executable skill used by the chat harness."""
        if safety_result.risk_level == RiskLevel.CRISIS or intent == Intent.CRISIS:
            return self._executable_by_name["crisis_escalation_skill"]
        return self._executable_by_name["general_support_skill"]

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
