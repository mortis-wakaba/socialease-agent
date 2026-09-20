"""Regression tests for optional-model and Compose configuration wiring."""

from pathlib import Path

from app.observability.readiness import _llm_configuration_check


def test_llm_readiness_distinguishes_disabled_and_degraded(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "false")
    monkeypatch.setenv("OUTPUT_GUARDRAIL_LLM_ENABLED", "false")

    disabled = _llm_configuration_check()

    assert disabled["ok"] is True
    assert disabled["generation"] == "disabled"
    assert disabled["output_guardrail_semantic"] == "disabled"
    assert "api_key" not in disabled

    monkeypatch.setenv("OUTPUT_GUARDRAIL_LLM_ENABLED", "true")
    degraded = _llm_configuration_check()
    assert degraded["ok"] is False
    assert degraded["output_guardrail_semantic"] == "degraded_unavailable"


def test_llm_readiness_reports_configured_models_without_provider_probe(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv("LLM_API_KEY", "secret-not-returned")
    monkeypatch.setenv("LLM_MODEL", "demo-model")
    monkeypatch.setenv("OUTPUT_GUARDRAIL_LLM_ENABLED", "true")
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://local.invalid/v1")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local-secret-not-returned")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")

    report = _llm_configuration_check()

    assert report == {
        "ok": True,
        "generation": "enabled_unprobed",
        "output_guardrail_semantic": "enabled_unprobed",
        "local_routing_and_summary": "enabled",
        "provider_availability_probe": "not_performed",
    }


def test_compose_files_forward_optional_model_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    required = {
        "OUTPUT_GUARDRAIL_LLM_ENABLED",
        "LLM_MAX_CONCURRENCY",
        "LLM_CONCURRENCY_WAIT_SECONDS",
        "LLM_MAX_QUEUE_SIZE",
        "LOCAL_LLM_ENABLED",
        "LOCAL_LLM_BASE_URL",
        "LOCAL_LLM_MODEL",
        "LOCAL_LLM_MAX_CONCURRENCY",
        "LOCAL_LLM_CONTEXT_SIZE",
    }

    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        rendered = (root / name).read_text(encoding="utf-8")
        missing = sorted(variable for variable in required if variable not in rendered)
        assert not missing, f"{name} does not forward: {missing}"


def test_ci_postgres_services_include_pgvector() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "image: postgres:16" not in workflow
    assert workflow.count("image: pgvector/pgvector:pg16") == 2
