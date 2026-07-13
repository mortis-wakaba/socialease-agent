"""Tests for the SocialEase skill registry used by the agent harness."""

from app.models import Intent, RiskLevel, SafetyResult
from app.skills.registry import SkillRegistry
from app.skills.support_rag import SupportRagSkill


def test_skill_registry_lists_core_agent_skills() -> None:
    registry = SkillRegistry()

    names = {descriptor.name for descriptor in registry.list_descriptors()}

    assert "crisis_escalation_skill" in names
    assert "general_support_skill" in names
    assert "roleplay_skill" in names
    assert "worksheet_skill" in names
    assert "exposure_planning_skill" in names
    assert "support_resource_rag_skill" in names


def test_skill_registry_resolves_crisis_skill_for_crisis_risk() -> None:
    registry = SkillRegistry()
    safety_result = SafetyResult(
        risk_level=RiskLevel.CRISIS,
        reason="Crisis expression detected.",
    )

    skill = registry.resolve_for_chat(Intent.EMOTIONAL_SUPPORT, safety_result)

    assert skill.descriptor.name == "crisis_escalation_skill"


def test_skill_registry_resolves_specialized_skill_for_non_crisis_chat() -> None:
    registry = SkillRegistry()
    safety_result = SafetyResult(
        risk_level=RiskLevel.LOW,
        reason="No crisis expression detected.",
    )

    skill = registry.resolve_for_chat(Intent.ROLEPLAY_PRACTICE, safety_result)

    assert skill.descriptor.name == "roleplay_skill"


def test_partial_registry_resolves_registered_skill_without_general_fallback() -> None:
    """Injected targeted registries should not eagerly require a fallback skill."""
    registry = SkillRegistry(executable_skills=[SupportRagSkill()])
    safety_result = SafetyResult(risk_level=RiskLevel.LOW, reason="safe")

    skill = registry.resolve_for_chat(Intent.CAMPUS_RESOURCE_QUERY, safety_result)

    assert skill.descriptor.name == "support_resource_rag_skill"


def test_descriptor_for_intent_exposes_specialized_skill_metadata() -> None:
    registry = SkillRegistry()

    descriptor = registry.descriptor_for_intent(Intent.CBT_WORKSHEET)

    assert descriptor is not None
    assert descriptor.name == "worksheet_skill"
    assert "worksheet" in descriptor.entrypoint
