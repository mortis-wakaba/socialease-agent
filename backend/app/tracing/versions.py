"""Build non-secret execution version metadata for product and eval traces."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.llm.factory import LLMConfig
from app.models_trace import ExecutionVersionInfo


TRACE_SCHEMA_VERSION = "trace-v2"
GUARDRAIL_POLICY_VERSION = "output-guardrail-2026-07-19"
SKILL_REGISTRY_VERSION = "bounded-skills-2026-07-19"

PROMPT_VERSIONS: dict[str, str] = {
    "safety_classifier": "safety-v1",
    "intent_router": "intent-v3",
    "support_generation": "support-v4",
    "roleplay": "roleplay-v5",
    "worksheet_extraction": "worksheet-v3",
    "resource_agent_loop": "resource-loop-v2",
    "output_guardrail": "output-guardrail-v6",
    "output_repair": "output-repair-v4",
    "roleplay_compaction": "roleplay-compaction-v1",
    "conversation_compaction": "conversation-compaction-v1",
    "memory_extraction": "memory-extraction-v3",
}

_PROMPTS_BY_SKILL: dict[str, tuple[str, ...]] = {
    "general_support_skill": ("support_generation",),
    "roleplay_skill": ("roleplay",),
    "worksheet_skill": ("worksheet_extraction",),
    "support_resource_rag_skill": ("resource_agent_loop",),
}


def build_execution_version_info(
    *,
    selected_skill: str | None = None,
    safety_llm_used: bool = False,
    intent_llm_used: bool = False,
    skill_llm_used: bool = False,
    output_semantic_checked: bool = False,
    output_repair_attempted: bool = False,
    memory_extraction_used: bool = False,
    eval_dataset_version: str | None = None,
    llm_config: LLMConfig | None = None,
) -> ExecutionVersionInfo:
    """Return bounded component identities without URLs, credentials, or prompt text."""
    config = llm_config or LLMConfig.from_env()
    prompt_names: list[str] = []
    if safety_llm_used:
        prompt_names.append("safety_classifier")
    if intent_llm_used:
        prompt_names.append("intent_router")
    if skill_llm_used:
        prompt_names.extend(_PROMPTS_BY_SKILL.get(selected_skill or "", ()))
    if output_semantic_checked:
        prompt_names.append("output_guardrail")
    if output_repair_attempted:
        prompt_names.append("output_repair")
    if memory_extraction_used:
        prompt_names.append("memory_extraction")
    prompt_versions = {
        name: PROMPT_VERSIONS[name]
        for name in dict.fromkeys(prompt_names)
    }
    return ExecutionVersionInfo(
        app_version=os.getenv("SOCIALEASE_APP_VERSION", "dev").strip() or "dev",
        trace_schema_version=TRACE_SCHEMA_VERSION,
        llm_provider=config.provider if config.enabled else None,
        llm_model=config.model if config.enabled else None,
        model_config_version=_model_config_version(config),
        prompt_versions=prompt_versions,
        guardrail_policy_version=GUARDRAIL_POLICY_VERSION,
        skill_registry_version=SKILL_REGISTRY_VERSION,
        eval_dataset_version=eval_dataset_version,
    )


def deterministic_eval_dataset_version(data_dir: Path | None = None) -> str:
    """Hash committed deterministic JSONL bytes into one reproducible dataset id."""
    resolved = data_dir or Path(__file__).resolve().parents[1] / "evals" / "data"
    digest = hashlib.sha256()
    for path in sorted(resolved.glob("*.jsonl")):
        if path.name.startswith("deepeval_"):
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()[:16]}"


def _model_config_version(config: LLMConfig) -> str:
    if not config.enabled:
        return "llm-disabled"
    safe_config = {
        "provider": config.provider,
        "model": config.model,
        "timeout_seconds": config.timeout_seconds,
        "retry_max_attempts": config.retry_max_attempts,
        "circuit_failure_threshold": config.circuit_failure_threshold,
        "max_concurrency": config.max_concurrency,
    }
    encoded = json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()[:16]}"
