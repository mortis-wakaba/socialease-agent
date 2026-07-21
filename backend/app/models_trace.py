"""Typed version metadata shared by product and evaluation traces."""

from pydantic import BaseModel, Field


class ExecutionVersionInfo(BaseModel):
    """Non-secret component versions needed to reproduce one trace or eval."""

    app_version: str = "dev"
    trace_schema_version: str = "trace-v2"
    llm_provider: str | None = None
    llm_model: str | None = None
    model_config_version: str = "llm-disabled"
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    guardrail_policy_version: str = "output-guardrail-v1"
    skill_registry_version: str = "bounded-skills-v1"
    eval_dataset_version: str | None = None
