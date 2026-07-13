"""Structured models for the bounded resource-guidance agent loop."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models_knowledge import Citation
from app.models_llm import LLMUsage


class ResourceLoopAction(str, Enum):
    """Read-only actions available to the resource-guidance loop."""

    SEARCH_SUPPORT_RESOURCES = "search_support_resources"
    SEARCH_PRACTICE_GUIDANCE = "search_practice_guidance"
    FINISH = "finish"


class ResourceLoopStopReason(str, Enum):
    """Stable reasons why the bounded loop stopped."""

    FINISHED = "FINISHED"
    LLM_DISABLED = "LLM_DISABLED"
    MAX_STEPS = "MAX_STEPS"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TOOL_ERROR = "TOOL_ERROR"


class ResourceLoopDecision(BaseModel):
    """One validated model decision inside the loop."""

    model_config = ConfigDict(extra="forbid")

    action: ResourceLoopAction
    reason: str = Field(min_length=1, max_length=300)
    query: str | None = Field(default=None, min_length=1, max_length=500)
    observation_ids: list[int] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ResourceLoopDecision":
        """Require the fields used by search and finish actions."""
        if self.action in {
            ResourceLoopAction.SEARCH_SUPPORT_RESOURCES,
            ResourceLoopAction.SEARCH_PRACTICE_GUIDANCE,
        }:
            if self.query is None:
                raise ValueError("Search actions require a query.")
            if self.observation_ids:
                raise ValueError("Search actions cannot select observations.")
        elif not self.observation_ids:
            raise ValueError("Finish requires at least one observation id.")
        return self


class ResourceLoopObservation(BaseModel):
    """Grounded result returned by one read-only retrieval tool."""

    observation_id: int
    tool: ResourceLoopAction
    query: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)


class ResourceLoopStep(BaseModel):
    """Trace-safe summary of one model decision and tool outcome."""

    step: int
    action: ResourceLoopAction
    reason: str
    query: str | None = None
    observation_id: int | None = None
    selected_observation_ids: list[int] = Field(default_factory=list)
    citation_count: int = 0
    unknown: bool | None = None
    outcome: str


class ResourceGuidanceLoopResult(BaseModel):
    """Final bounded result returned to the Support RAG skill."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)
    blocked: bool = False
    steps: list[ResourceLoopStep] = Field(default_factory=list)
    stop_reason: ResourceLoopStopReason
    used_agent_loop: bool
    fallback_used: bool
    llm_usage: LLMUsage = Field(default_factory=LLMUsage)
