"""Application-owned metadata contract shared by memory writes and reads."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models_long_term_memory import MemoryEvidenceType, MemorySourceType
from app.models_scenario import ScenarioSpec, SocialSkillCode
from app.services.scenario_interpreter import ScenarioInterpreter


MEMORY_METADATA_CONTRACT_VERSION = "memory-metadata-v1"


class MemoryOutcomeCode(str, Enum):
    """Explicit outcome evidence; never a model-inferred psychological label."""

    HELPFUL = "helpful"
    COMPLETED = "completed"
    NEUTRAL = "neutral"
    UNHELPFUL = "unhelpful"


SOCIAL_SKILL_LABELS_ZH: dict[SocialSkillCode, str] = {
    SocialSkillCode.CONVERSATION_INITIATION: "开启对话",
    SocialSkillCode.QUESTION_ASKING: "提出问题",
    SocialSkillCode.ASSERTIVE_EXPRESSION: "清晰表达观点",
    SocialSkillCode.BOUNDARY_SETTING: "表达边界",
    SocialSkillCode.SPECIFIC_REQUEST: "提出具体请求",
    SocialSkillCode.DISAGREEMENT: "表达不同意见",
    SocialSkillCode.EMPATHY: "回应与共情",
    SocialSkillCode.CONFLICT_REPAIR: "修复冲突",
    SocialSkillCode.INVITATION: "发出邀请",
    SocialSkillCode.SELF_INTRODUCTION: "自我介绍",
    SocialSkillCode.CONVERSATION_EXIT: "结束对话",
    SocialSkillCode.COLLABORATIVE_PROBLEM_SOLVING: "协作解决问题",
}


class MemoryRetrievalMetadata(BaseModel):
    """Privacy-minimized fields the application may attach to retrieval."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scenario_type: str | None = Field(default=None, max_length=128)
    scenario_id: str | None = Field(default=None, max_length=128)
    practice_thread_id: str | None = Field(default=None, max_length=128)
    skill_codes: list[SocialSkillCode] = Field(default_factory=list, max_length=5)
    context_tags: list[str] = Field(default_factory=list, max_length=5)


class MemoryWriteMetadata(MemoryRetrievalMetadata):
    """Trusted source and outcome fields attached at the durable-write boundary."""

    source_type: MemorySourceType
    evidence_type: MemoryEvidenceType
    outcome_code: MemoryOutcomeCode = MemoryOutcomeCode.NEUTRAL


def retrieval_metadata_from_description(
    description: str,
    *,
    practice_thread_id: str | None = None,
    scenario_id: str | None = None,
) -> MemoryRetrievalMetadata:
    """Interpret one current request into the same facets used for writes."""
    scenario = ScenarioInterpreter().interpret(description=description)
    return retrieval_metadata_from_scenario(
        scenario,
        practice_thread_id=practice_thread_id,
        scenario_id=scenario_id,
    )


def retrieval_metadata_from_scenario(
    scenario: ScenarioSpec,
    *,
    practice_thread_id: str | None = None,
    scenario_id: str | None = None,
) -> MemoryRetrievalMetadata:
    """Project a scenario without retaining its free-text description."""
    return MemoryRetrievalMetadata(
        scenario_type=_scenario_type(scenario),
        scenario_id=scenario_id,
        practice_thread_id=practice_thread_id,
        skill_codes=scenario.skill_codes,
        context_tags=scenario.context_tags,
    )


def write_metadata_from_scenario(
    scenario: ScenarioSpec,
    *,
    source_type: MemorySourceType,
    evidence_type: MemoryEvidenceType,
    outcome_code: MemoryOutcomeCode,
    practice_thread_id: str | None = None,
) -> MemoryWriteMetadata:
    """Build trusted write metadata from a completed product event."""
    retrieval = retrieval_metadata_from_scenario(
        scenario,
        practice_thread_id=practice_thread_id,
        scenario_id=scenario.scenario_id,
    )
    return MemoryWriteMetadata(
        **retrieval.model_dump(),
        source_type=source_type,
        evidence_type=evidence_type,
        outcome_code=outcome_code,
    )


def write_metadata_from_description(
    description: str,
    *,
    source_type: MemorySourceType,
    evidence_type: MemoryEvidenceType,
    outcome_code: MemoryOutcomeCode,
    practice_thread_id: str | None = None,
) -> MemoryWriteMetadata:
    """Build trusted metadata when a module exposes only a bounded description."""
    scenario = ScenarioInterpreter().interpret(description=description)
    retrieval = retrieval_metadata_from_scenario(
        scenario,
        practice_thread_id=practice_thread_id,
        scenario_id=None,
    )
    return MemoryWriteMetadata(
        **retrieval.model_dump(),
        source_type=source_type,
        evidence_type=evidence_type,
        outcome_code=outcome_code,
    )


def outcome_context_tags(metadata: MemoryWriteMetadata) -> list[str]:
    """Persist outcome in the existing bounded tag column during schema rollout."""
    outcome_tag = f"outcome:{metadata.outcome_code.value}"
    return list(dict.fromkeys([*metadata.context_tags[:4], outcome_tag]))


def social_skill_label_zh(skill: SocialSkillCode) -> str:
    """Return the stable Chinese label used in structured representations."""
    return SOCIAL_SKILL_LABELS_ZH[skill]


def _scenario_type(scenario: ScenarioSpec) -> str | None:
    """Keep scenario_type as a migration alias for interaction_mode."""
    value = scenario.interaction_mode.value
    return None if value == "unspecified" else value
