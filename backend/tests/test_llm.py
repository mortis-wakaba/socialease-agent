"""Tests for optional LLM configuration and OpenAI-compatible clients."""

import asyncio
import json

import httpx
import pytest

from app.llm.factory import LLMConfig, create_llm_client, create_local_llm_client
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
    assert client.json_mode_enabled is False
    assert client.concurrency_limiter.max_concurrency == 2


@pytest.mark.anyio
async def test_factory_reuses_an_injected_http_client() -> None:
    config = LLMConfig(
        enabled=True,
        provider="openai_compatible",
        base_url="https://api.deepseek.com",
        api_key="demo-key",
        model="deepseek-chat",
        timeout_seconds=12,
    )
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )

    async with http_client:
        client = create_llm_client(config, http_client=http_client)
        assert isinstance(client, OpenAICompatibleLLMClient)
        assert client.http_client is http_client


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


def test_llm_config_enables_json_mode_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_JSON_MODE_ENABLED", "true")

    config = LLMConfig.from_env()

    assert config.json_mode_enabled is True


def test_local_llm_config_is_independent_from_hosted_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.5:4b")

    config = LLMConfig.local_from_env()
    client = create_local_llm_client(config)

    assert config.enabled is True
    assert config.model == "qwen3.5:4b"
    assert config.json_mode_enabled is True
    assert config.reasoning_effort == "none"
    assert config.retry_max_attempts == 1
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.model == "qwen3.5:4b"
    assert client.base_url == "http://127.0.0.1:8080/v1"


def test_local_llm_factory_stays_disabled_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_LLM_ENABLED", raising=False)

    assert create_local_llm_client() is None


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
        assert '"response_format"' not in payload
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
async def test_openai_compatible_client_streams_text_deltas() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["stream"] is True
        assert payload["messages"][1]["content"] == "user"
        body = (
            'data: {"choices":[{"delta":{"content":"第一句。"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"第二句。"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            http_client=http_client,
        )
        chunks = [
            chunk
            async for chunk in client.stream_text(
                system_prompt="system",
                user_prompt="user",
            )
        ]

    assert chunks == ["第一句。", "第二句。"]


@pytest.mark.anyio
async def test_openai_compatible_client_generates_json_mode_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["reasoning_effort"] == "none"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok":true}'}}]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.deepseek.com",
            api_key="demo-key",
            model="deepseek-chat",
            json_mode_enabled=True,
            reasoning_effort="none",
            http_client=http_client,
        )
        result = await client.generate_json_text(
            system_prompt="Return JSON only.",
            user_prompt="Return one JSON object.",
        )

    assert result == '{"ok":true}'


def test_openai_compatible_client_rejects_unknown_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="unsupported reasoning_effort"):
        OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            reasoning_effort="extreme",
        )


@pytest.mark.anyio
async def test_openai_compatible_client_rejects_disabled_json_mode() -> None:
    client = OpenAICompatibleLLMClient(
        base_url="https://api.example.com",
        api_key="demo-key",
        model="demo-model",
    )

    with pytest.raises(ValueError, match="JSON mode is not enabled"):
        await client.generate_json_text(
            system_prompt="Return JSON only.",
            user_prompt="Return one JSON object.",
        )


@pytest.mark.anyio
async def test_openai_compatible_client_sends_tools_and_parses_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["tool_choice"] == "auto"
        assert payload["parallel_tool_calls"] is False
        assert payload["tools"][0]["function"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-memory-1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_practice_memory",
                                        "arguments": json.dumps(
                                            {"query": "过去课堂发言练习"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_practice_memory",
                "description": "Search memory.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = OpenAICompatibleLLMClient(
            base_url="https://api.example.com",
            api_key="demo-key",
            model="demo-model",
            http_client=http_client,
        )
        response = await client.complete_with_tools(
            messages=[{"role": "user", "content": "以前练过什么？"}],
            tools=tools,
        )

    assert response.content is None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].call_id == "call-memory-1"
    assert response.tool_calls[0].arguments == {
        "query": "过去课堂发言练习"
    }


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

    async def fake_record_llm_concurrency_saturation() -> None:
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
