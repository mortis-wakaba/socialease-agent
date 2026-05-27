"""Pydantic models for harness capability discovery."""

from pydantic import BaseModel, Field

from app.models import Intent
from app.models_knowledge import KnowledgeBaseType


class HarnessSkillCapability(BaseModel):
    """Public metadata for one registered SocialEase skill."""

    name: str
    description: str
    supported_intents: list[Intent]
    entrypoint: str
    safety_notes: str
    has_manifest: bool


class HarnessCapabilitiesResponse(BaseModel):
    """Capability discovery response for the SocialEase agent harness."""

    harness: str = "SocialEase Agent Harness"
    design: str = "Model + Harness"
    runtime_loop: list[str] = Field(default_factory=list)
    permission_actions: list[str] = Field(default_factory=list)
    skills: list[HarnessSkillCapability] = Field(default_factory=list)
    knowledge_layers: list[KnowledgeBaseType] = Field(default_factory=list)
    observation: list[str] = Field(default_factory=list)
    safety_boundaries: list[str] = Field(default_factory=list)


class HarnessMetricsResponse(BaseModel):
    """Lightweight aggregate metrics for recent harness runs."""

    window_size: int
    total_runs: int
    crisis_runs: int
    fallback_runs: int
    average_latency_ms: float
    intent_counts: dict[str, int] = Field(default_factory=dict)
    selected_agent_counts: dict[str, int] = Field(default_factory=dict)
