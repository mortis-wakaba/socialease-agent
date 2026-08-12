"""OpenAI-compatible HTTP adapter for hosted LLM providers."""

import json
from collections.abc import AsyncIterator, Callable
from typing import TypeVar

import httpx

from app.llm.base import LLMToolCall, LLMToolResponse
from app.llm.retry import (
    CircuitBreaker,
    PermanentProviderError,
    ProviderConcurrencyLimiter,
    RetryPolicy,
    classify_httpx_error,
    retry_async,
)


T = TypeVar("T")


class OpenAICompatibleLLMClient:
    """Call providers exposing an OpenAI-compatible chat-completions API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        json_mode_enabled: bool = False,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        concurrency_limiter: ProviderConcurrencyLimiter | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.json_mode_enabled = json_mode_enabled
        self.http_client = http_client
        self.retry_policy = retry_policy or RetryPolicy()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.concurrency_limiter = concurrency_limiter or ProviderConcurrencyLimiter(
            max_concurrency=0
        )

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        """Generate text through the chat-completions compatible endpoint."""
        return await self._generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format=None,
        )

    async def generate_json_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        """Generate a JSON object when the configured provider supports JSON mode."""
        if not self.json_mode_enabled:
            raise ValueError("JSON mode is not enabled for this LLM provider")
        return await self._generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

    async def stream_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Yield OpenAI-compatible chat-completion text deltas."""
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.circuit_breaker.before_call()
        async with self.concurrency_limiter.slot():
            try:
                if self.http_client is not None:
                    async for delta in self._stream_post(
                        self.http_client,
                        payload,
                        headers,
                    ):
                        yield delta
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        async for delta in self._stream_post(client, payload, headers):
                            yield delta
            except httpx.HTTPError as exc:
                self.circuit_breaker.record_failure()
                raise classify_httpx_error(exc) from exc
            except Exception:
                self.circuit_breaker.record_failure()
                raise
            else:
                self.circuit_breaker.record_success()

    async def _generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        response_format: dict[str, str] | None,
    ) -> str:
        """Share retry and response parsing across text and JSON modes."""
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_client is not None:
            return await self.concurrency_limiter.run(
                lambda: retry_async(
                    lambda: self._post(
                        self.http_client,
                        payload,
                        headers,
                        _text_response,
                    ),
                    retry_policy=self.retry_policy,
                    circuit_breaker=self.circuit_breaker,
                )
            )

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await self.concurrency_limiter.run(
                lambda: retry_async(
                    lambda: self._post(client, payload, headers, _text_response),
                    retry_policy=self.retry_policy,
                    circuit_breaker=self.circuit_breaker,
                )
            )

    async def complete_with_tools(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.0,
        tool_choice: str = "auto",
    ) -> LLMToolResponse:
        """Complete one native Chat Completions function-calling turn."""
        if not tools:
            raise ValueError("native Tool completion requires at least one Tool")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("unsupported tool_choice")
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_client is not None:
            return await self.concurrency_limiter.run(
                lambda: retry_async(
                    lambda: self._post(
                        self.http_client,
                        payload,
                        headers,
                        _tool_response,
                    ),
                    retry_policy=self.retry_policy,
                    circuit_breaker=self.circuit_breaker,
                )
            )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await self.concurrency_limiter.run(
                lambda: retry_async(
                    lambda: self._post(
                        client,
                        payload,
                        headers,
                        _tool_response,
                    ),
                    retry_policy=self.retry_policy,
                    circuit_breaker=self.circuit_breaker,
                )
            )

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        headers: dict[str, str],
        parser: Callable[[dict[str, object]], T],
    ) -> T:
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise classify_httpx_error(exc) from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise classify_httpx_error(exc) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise PermanentProviderError("LLM provider returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise PermanentProviderError("LLM provider returned an invalid object.")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise PermanentProviderError("LLM response did not include choices.")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise PermanentProviderError("LLM response choice was invalid.")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise PermanentProviderError("LLM response did not include a message.")
        return parser(message)

    async def _stream_post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> AsyncIterator[str]:
        """Parse one OpenAI-compatible SSE response without buffering it."""
        received_delta = False
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        ) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise classify_httpx_error(exc) from exc
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                delta = _stream_delta(data)
                if delta:
                    received_delta = True
                    yield delta
        if not received_delta:
            raise PermanentProviderError(
                "LLM streaming response did not include text content."
            )


def _text_response(message: dict[str, object]) -> str:
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise PermanentProviderError("LLM response did not include text content.")
    return content.strip()


def _tool_response(message: dict[str, object]) -> LLMToolResponse:
    raw_content = message.get("content")
    content = raw_content.strip() if isinstance(raw_content, str) else None
    raw_calls = message.get("tool_calls", [])
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise PermanentProviderError("LLM tool_calls was not an array.")
    tool_calls: list[LLMToolCall] = []
    for item in raw_calls:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise PermanentProviderError("LLM returned an invalid Tool call.")
        call_id = item.get("id")
        function = item.get("function")
        if not isinstance(call_id, str) or not isinstance(function, dict):
            raise PermanentProviderError("LLM Tool call fields were invalid.")
        name = function.get("name")
        raw_arguments = function.get("arguments")
        if not isinstance(name, str) or not isinstance(raw_arguments, str):
            raise PermanentProviderError("LLM function fields were invalid.")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise PermanentProviderError(
                "LLM Tool arguments were invalid JSON."
            ) from exc
        if not isinstance(arguments, dict):
            raise PermanentProviderError("LLM Tool arguments must be an object.")
        tool_calls.append(
            LLMToolCall(call_id=call_id, name=name, arguments=arguments)
        )
    if len({item.call_id for item in tool_calls}) != len(tool_calls):
        raise PermanentProviderError("LLM Tool call ids must be unique.")
    if not content and not tool_calls:
        raise PermanentProviderError(
            "LLM response included neither text nor Tool calls."
        )
    return LLMToolResponse(content=content, tool_calls=tool_calls)


def _stream_delta(data: str) -> str:
    """Return one validated content delta from an SSE JSON payload."""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise PermanentProviderError("LLM streaming event was invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise PermanentProviderError("LLM streaming event was not an object.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        raise PermanentProviderError("LLM streaming choice was invalid.")
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""
