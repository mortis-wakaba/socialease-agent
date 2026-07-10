"""Tests for optional LLM configuration and OpenAI-compatible clients."""

import asyncio

import httpx
import pytest

from app.llm.factory import LLMConfig, create_llm_client
from app.llm.openai_compatible import OpenAICompatibleLLMClient
from app.llm.retry import (
    CircuitBreaker,
    ProviderConcurrencyLimitError,
    ProviderCircuitOpenError,
    ProviderConcurrencyLimiter,
    RetryPolicy,
    TransientProviderError,
)


def test_factory_returns_none_when_llm_disabled() -> None:
    config = LLMConfig(
        enabled=False,
        provider="openai_compatible",
        base_url=None,
        api_key=None,
        model=None,
        timeout_seconds=30,
    )

    assert create_llm_client(config) is None


def test_factory_builds_openai_compatible_client() -> None:
    config = LLMConfig(
        enabled=True,
        provider="openai_compatible",
        base_url="https://api.deepseek.com/",
        api_key="demo-key",
        model="deepseek-chat",
        timeout_seconds=12,
        max_concurrency=2,
    )

    client = create_llm_client(config)

    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.base_url == "https://api.deepseek.com"
    assert client.model == "deepseek-chat"
    assert client.timeout_seconds == 12
    assert client.concurrency_limiter.max_concurrency == 2


def test_llm_config_keeps_legacy_concurrency_env_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "demo-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.delenv("LLM_MAX_CONCURRENCY", raising=False)
    monkeypatch.setenv("SOCIALEASE_LLM_MAX_CONCURRENCY", "3")

    config = LLMConfig.from_env()

    assert config.max_concurrency == 3


def test_factory_rejects_incomplete_enabled_config() -> None:
    config = LLMConfig(
        enabled=True,
        provider="openai_compatible",
        base_url="https://api.deepseek.com",
        api_key=None,
        model="deepseek-chat",
        timeout_seconds=30,
    )

    with pytest.raises(ValueError, match="base_url, api_key, or model"):
        create_llm_client(config)


@pytest.mark.anyio
async def test_openai_compatible_client_generates_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.example.com/chat/completions")
        assert request.headers["authorization"] == "Bearer demo-key"
        payload = request.read().decode()
        assert '"model":"demo-model"' in payload
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  hello from llm  "}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            http_client=http_client,
        )
        result = await client.generate_text(
            system_prompt="system",
            user_prompt="user",
        )

    assert result == "hello from llm"


@pytest.mark.anyio
async def test_openai_compatible_client_retries_transient_provider_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, request=request, json={"error": "temporary"})
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "recovered"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            http_client=http_client,
            retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0),
        )
        result = await client.generate_text(system_prompt="system", user_prompt="user")

    assert result == "recovered"
    assert calls == 2


@pytest.mark.anyio
async def test_openai_compatible_client_opens_circuit_after_repeated_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request, json={"error": "temporary"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            http_client=http_client,
            retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0),
            circuit_breaker=CircuitBreaker(failure_threshold=1, recovery_seconds=60),
        )
        with pytest.raises(Exception, match="transient HTTP 500"):
            await client.generate_text(system_prompt="system", user_prompt="user")
        with pytest.raises(ProviderCircuitOpenError):
            await client.generate_text(system_prompt="system", user_prompt="user")

    assert calls == 2


@pytest.mark.anyio
async def test_openai_compatible_client_classifies_timeout_as_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            http_client=http_client,
            retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
        )
        with pytest.raises(TransientProviderError, match="provider timed out"):
            await client.generate_text(system_prompt="system", user_prompt="user")


@pytest.mark.anyio
async def test_openai_compatible_client_enforces_global_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saturation_events = 0

    def fake_record_llm_concurrency_saturation() -> None:
        nonlocal saturation_events
        saturation_events += 1

    monkeypatch.setattr(
        "app.observability.runtime_events.record_llm_concurrency_saturation",
        fake_record_llm_concurrency_saturation,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            http_client=http_client,
            retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
            concurrency_limiter=ProviderConcurrencyLimiter(
                max_concurrency=1,
                acquire_timeout_seconds=0.001,
            ),
        )
        first = asyncio.create_task(
            client.generate_text(system_prompt="system", user_prompt="first")
        )
        await asyncio.sleep(0)

        with pytest.raises(ProviderConcurrencyLimitError):
            await client.generate_text(system_prompt="system", user_prompt="second")

        assert await first == "ok"
        assert saturation_events == 1
