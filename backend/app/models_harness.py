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
    hook_blocked_runs: int = 0
    memory_write_blocked_runs: int = 0
    average_latency_ms: float
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    intent_counts: dict[str, int] = Field(default_factory=dict)
    risk_counts: dict[str, int] = Field(default_factory=dict)
    selected_agent_counts: dict[str, int] = Field(default_factory=dict)
    permission_counts: dict[str, int] = Field(default_factory=dict)
    product_boundary_eval_counts: dict[str, int] = Field(default_factory=dict)
    runtime_event_counts: dict[str, int] = Field(default_factory=dict)
    rate_limit_hits: int = 0
    llm_concurrency_saturation: int = 0
    slow_request_count: int = 0
    memory_export_count: int = 0
    memory_delete_count: int = 0
    memory_preferences_saved_count: int = 0
    memory_preferences_disabled_count: int = 0
    auth_rate_limit_hits: int = 0
    auth_failed_login_count: int = 0
    auth_lockout_count: int = 0
