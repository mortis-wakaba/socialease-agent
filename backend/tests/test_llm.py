"""Tests for optional LLM configuration and OpenAI-compatible clients."""

import httpx
import pytest

from app.llm.factory import LLMConfig, create_llm_client
from app.llm.openai_compatible import OpenAICompatibleLLMClient


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
    )

    client = create_llm_client(config)

    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.base_url == "https://api.deepseek.com"
    assert client.model == "deepseek-chat"
    assert client.timeout_seconds == 12


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
