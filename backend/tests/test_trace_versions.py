"""Tests for non-secret product and evaluation trace version metadata."""

from pathlib import Path

from app.llm.factory import LLMConfig
from app.tracing.versions import (
    build_execution_version_info,
    deterministic_eval_dataset_version,
)


def _enabled_config() -> LLMConfig:
    return LLMConfig(
        enabled=True,
        provider="openai_compatible",
        base_url="https://private-gateway.example/v1",
        api_key="sk-private-fake",
        model="demo-model-v2",
        timeout_seconds=12.0,
    )


def test_execution_version_records_component_ids_without_secrets() -> None:
    version = build_execution_version_info(
        selected_skill="general_support_skill",
        safety_llm_used=True,
        intent_llm_used=True,
        skill_llm_used=True,
        output_semantic_checked=True,
        output_repair_attempted=True,
        llm_config=_enabled_config(),
    )
    payload = version.model_dump_json()

    assert version.llm_provider == "openai_compatible"
    assert version.llm_model == "demo-model-v2"
    assert version.model_config_version.startswith("sha256:")
    assert version.prompt_versions == {
        "safety_classifier": "safety-v1",
        "intent_router": "intent-v3",
        "support_generation": "support-v4",
        "output_guardrail": "output-guardrail-v6",
        "output_repair": "output-repair-v4",
    }
    assert "private-gateway" not in payload
    assert "sk-private" not in payload


def test_execution_version_only_lists_prompts_actually_used_by_selected_route() -> None:
    version = build_execution_version_info(
        selected_skill="support_resource_rag_skill",
        skill_llm_used=True,
        llm_config=LLMConfig(
            enabled=False,
            provider="openai_compatible",
            base_url=None,
            api_key=None,
            model=None,
            timeout_seconds=30.0,
        ),
    )

    assert version.llm_model is None
    assert version.model_config_version == "llm-disabled"
    assert version.prompt_versions == {
        "resource_agent_loop": "resource-loop-v2"
    }


def test_eval_dataset_version_changes_with_committed_fixture_bytes(tmp_path: Path) -> None:
    (tmp_path / "intent.jsonl").write_text('{"id":"one"}\n', encoding="utf-8")
    first = deterministic_eval_dataset_version(tmp_path)

    (tmp_path / "intent.jsonl").write_text('{"id":"two"}\n', encoding="utf-8")
    second = deterministic_eval_dataset_version(tmp_path)

    assert first.startswith("sha256:")
    assert first != second


def test_eval_dataset_version_ignores_paid_deepeval_fixtures(tmp_path: Path) -> None:
    (tmp_path / "intent.jsonl").write_text('{"id":"one"}\n', encoding="utf-8")
    first = deterministic_eval_dataset_version(tmp_path)
    (tmp_path / "deepeval_resource.jsonl").write_text(
        '{"demo":true}\n',
        encoding="utf-8",
    )

    assert deterministic_eval_dataset_version(tmp_path) == first
