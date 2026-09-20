"""Tests for Ollama warm-up, residency, readiness, and slot contracts."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm.local_runtime import LocalLLMRuntime, LocalRuntimeSettings


def _configure_ollama(monkeypatch: pytest.MonkeyPatch, *, slots: int = 2) -> None:
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("LOCAL_LLM_RUNTIME", "ollama")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "ollama")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.5:4b")
    monkeypatch.setenv("LOCAL_LLM_READINESS_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_LLM_KEEP_ALIVE", "-1")
    monkeypatch.setenv("LOCAL_LLM_OLLAMA_SLOTS", str(slots))
    monkeypatch.setenv("LOCAL_LLM_MAX_CONCURRENCY", str(slots))
    monkeypatch.setenv("LOCAL_LLM_MAX_QUEUE_SIZE", "3")
    monkeypatch.setenv("LOCAL_LLM_RECOVERY_INTERVAL_SECONDS", "3600")


@pytest.mark.anyio
async def test_ollama_start_warms_and_readiness_verifies_residence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_ollama(monkeypatch)
    warm_payload: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            warm_payload.update(json.loads(request.content))
            return httpx.Response(200, request=request, json={"done": True, "response": "O"})
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                request=request,
                json={"models": [{"name": "qwen3.5:4b"}]},
            )
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = LocalLLMRuntime(http_client=client)
        await runtime.start()
        health = await runtime.health()
        await runtime.close()

    assert warm_payload["model"] == "qwen3.5:4b"
    assert warm_payload["keep_alive"] == -1
    assert warm_payload["stream"] is False
    assert health["ok"] is True
    assert health["resident"] is True
    assert health["inference_verified"] is True
    assert health["slot_contract_ok"] is True
    assert health["capacity"] == {
        "max_concurrency": 2,
        "max_queue_size": 3,
        "active": 0,
        "waiting": 0,
        "available": 2,
        "queue_available": 3,
    }


@pytest.mark.anyio
async def test_readiness_fails_when_model_is_not_resident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_ollama(monkeypatch)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/api/generate":
            calls += 1
            return httpx.Response(200, request=request, json={"done": True})
        return httpx.Response(200, request=request, json={"models": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = LocalLLMRuntime(http_client=client)
        await runtime.start()
        health = await runtime.health()
        await runtime.close()

    assert calls == 1
    assert health["ok"] is False
    assert health["healthy"] is False
    assert health["resident"] is False
    assert health["error"] == "model_not_resident"


def test_external_local_runtime_is_not_required_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("LOCAL_LLM_RUNTIME", "external")
    monkeypatch.delenv("LOCAL_LLM_READINESS_REQUIRED", raising=False)

    settings = LocalRuntimeSettings.from_env()

    assert settings.enabled is False
    assert settings.required is False


@pytest.mark.anyio
async def test_llama_server_warmup_and_readiness_use_actual_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("LOCAL_LLM_RUNTIME", "llama_server")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local-secret")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.5:4b")
    monkeypatch.setenv("LOCAL_LLM_READINESS_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_LLM_SERVER_SLOTS", "4")
    monkeypatch.setenv("LOCAL_LLM_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("LOCAL_LLM_RECOVERY_INTERVAL_SECONDS", "3600")
    observed_authorization: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_authorization.append(request.headers.get("authorization"))
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "O"}}]},
            )
        if request.url.path == "/health":
            return httpx.Response(200, request=request, json={"status": "ok"})
        if request.url.path == "/slots":
            return httpx.Response(
                200,
                request=request,
                json=[{"id": index} for index in range(4)],
            )
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = LocalLLMRuntime(http_client=client)
        await runtime.start()
        health = await runtime.health()
        await runtime.close()

    assert health["ok"] is True
    assert health["runtime"] == "llama_server"
    assert health["resident"] is True
    assert health["inference_verified"] is True
    assert health["observed_server_slots"] == 4
    assert health["slot_contract_ok"] is True
    assert set(observed_authorization) == {"Bearer local-secret"}


@pytest.mark.anyio
async def test_llama_server_readiness_rejects_slot_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("LOCAL_LLM_RUNTIME", "llama_server")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.5:4b")
    monkeypatch.setenv("LOCAL_LLM_READINESS_REQUIRED", "true")
    monkeypatch.setenv("LOCAL_LLM_SERVER_SLOTS", "4")
    monkeypatch.setenv("LOCAL_LLM_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("LOCAL_LLM_RECOVERY_INTERVAL_SECONDS", "3600")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, request=request, json={"choices": [{}]})
        if request.url.path == "/slots":
            return httpx.Response(200, request=request, json=[{"id": 0}])
        return httpx.Response(200, request=request, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = LocalLLMRuntime(http_client=client)
        await runtime.start()
        health = await runtime.health()
        await runtime.close()

    assert health["observed_server_slots"] == 1
    assert health["slot_contract_ok"] is False
    assert health["ok"] is False
