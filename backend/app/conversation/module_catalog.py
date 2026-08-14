"""Canonical metadata and pure projections for conversation modules."""

from dataclasses import dataclass

from app.models import Intent
from app.models_conversation import (
    ExposureParameters,
    ModuleParameters,
    ModuleProposal,
    ModuleProposalReason,
    ModuleType,
    ResourceParameters,
    RoleplayParameters,
    WorksheetParameters,
)


@dataclass(frozen=True)
class _ModuleDefinition:
    """Stable metadata shared by routing, proposals, and memory selection."""

    intent: Intent
    skill_name: str | None
    proposal_reason: ModuleProposalReason
    display_label: str


_MODULE_DEFINITIONS = {
    ModuleType.ROLEPLAY: _ModuleDefinition(
        intent=Intent.ROLEPLAY_PRACTICE,
        skill_name="roleplay_skill",
        proposal_reason=ModuleProposalReason.EXPLICIT_PRACTICE_REQUEST,
        display_label="角色扮演",
    ),
    ModuleType.WORKSHEET: _ModuleDefinition(
        intent=Intent.CBT_WORKSHEET,
        skill_name="worksheet_skill",
        proposal_reason=ModuleProposalReason.STRUCTURED_REFLECTION_MAY_HELP,
        display_label="结构化想法记录",
    ),
    ModuleType.EXPOSURE: _ModuleDefinition(
        intent=Intent.EXPOSURE_PLANNING,
        skill_name="exposure_planning_skill",
        proposal_reason=ModuleProposalReason.GRADED_PRACTICE_MAY_HELP,
        display_label="分级社交练习",
    ),
    ModuleType.RESOURCE: _ModuleDefinition(
        intent=Intent.CAMPUS_RESOURCE_QUERY,
        skill_name=None,
        proposal_reason=ModuleProposalReason.RESOURCE_LOOKUP_REQUESTED,
        display_label="支持资源查询",
    ),
}

_MODULE_BY_INTENT = {
    definition.intent: module_type
    for module_type, definition in _MODULE_DEFINITIONS.items()
}


def module_type_for_intent(intent: Intent) -> ModuleType | None:
    """Return the conversation module offered for one routed intent."""
    return _MODULE_BY_INTENT.get(intent)


def intent_for_module_type(module_type: ModuleType) -> Intent:
    """Return the canonical intent represented by one module type."""
    return _MODULE_DEFINITIONS[module_type].intent


def module_skill_name(module_type: ModuleType) -> str | None:
    """Return the Skill name used to select stable module context."""
    return _MODULE_DEFINITIONS[module_type].skill_name


def build_module_parameters(
    module_type: ModuleType,
    message: str,
) -> ModuleParameters:
    """Build the bounded proposal parameters for one module type."""
    if module_type is ModuleType.ROLEPLAY:
        return RoleplayParameters(scenario_description=message)
    if module_type is ModuleType.WORKSHEET:
        return WorksheetParameters(situation=message)
    if module_type is ModuleType.EXPOSURE:
        return ExposureParameters(goal=message)
    return ResourceParameters(query=message)


def module_start_message(proposal: ModuleProposal) -> str:
    """Return the validated module input represented by a proposal."""
    parameters = proposal.bounded_parameters
    if isinstance(parameters, RoleplayParameters):
        return parameters.scenario_description
    if isinstance(parameters, WorksheetParameters):
        return parameters.situation
    if isinstance(parameters, ExposureParameters):
        return parameters.goal
    return parameters.query


def module_memory_request_context(
    module_type: ModuleType,
    message: str,
    parameters: ModuleParameters | None = None,
) -> dict[str, object]:
    """Expose only validated selector fields used by the target module Skill."""
    if module_type is ModuleType.ROLEPLAY:
        if isinstance(parameters, RoleplayParameters):
            return {
                "scenario": parameters.scenario_description,
                "difficulty": parameters.difficulty,
            }
        return {"scenario": message}
    if module_type is ModuleType.EXPOSURE:
        if isinstance(parameters, ExposureParameters):
            context: dict[str, object] = {"target_scenario": parameters.goal}
            if parameters.starting_anxiety is not None:
                context["current_anxiety_level"] = parameters.starting_anxiety
            return context
        return {"target_scenario": message}
    return {}


def module_proposal_reason(module_type: ModuleType) -> ModuleProposalReason:
    """Return the bounded reason code for offering one module."""
    return _MODULE_DEFINITIONS[module_type].proposal_reason


def module_proposal_copy(module_type: ModuleType) -> str:
    """Return the non-starting confirmation copy for one module proposal."""
    label = _MODULE_DEFINITIONS[module_type].display_label
    return (
        f"这个请求可能适合进入“{label}”模块。"
        "你可以选择进入，也可以继续普通对话；在你确认前不会启动模块。"
    )
