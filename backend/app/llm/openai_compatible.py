"""OpenAI-compatible HTTP adapter for hosted LLM providers."""

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import TypeVar

import httpx

from app.llm.base import LLMToolCall, LLMToolResponse, LLMToolStreamEvent
from app.llm.retry import (
    CircuitBreaker,
    PermanentProviderError,
    ProviderConcurrencyLimiter,
    RetryPolicy,
    classify_httpx_error,
    retry_async,
)
from app.observability.turn_latency import (
    current_turn_stage,
    mark_turn_flag,
    record_turn_counter,
    record_turn_stage,
)


T = TypeVar("T")


@dataclass
class _HTTPTraceTimings:
    """Collect stable httpcore phase durations without retaining request data."""

    started: dict[str, float] = field(default_factory=dict)
    durations_ms: dict[str, float] = field(default_factory=dict)

    async def __call__(self, event_name: str, info: dict[str, object]) -> None:
        del info
        event, separator, status = event_name.rpartition(".")
        if not separator:
            return
        if status == "started":
            self.started[event] = perf_counter()
            return
        if status not in {"complete", "failed"}:
            return
        started = self.started.pop(event, None)
        metric = _http_trace_metric(event)
        if started is None or metric is None:
            return
        elapsed_ms = (perf_counter() - started) * 1000
        self.durations_ms[metric] = self.durations_ms.get(metric, 0.0) + elapsed_ms

    def flush(self, operation: str) -> None:
        """Record collected phases on the active conversation turn."""
        for metric, latency_ms in self.durations_ms.items():
            record_turn_stage(f"llm_http.{operation}.{metric}", latency_ms)


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
        reasoning_effort: str | None = None,
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
        if reasoning_effort not in {None, "none", "low", "medium", "high", "max"}:
            raise ValueError("unsupported reasoning_effort")
        self.reasoning_effort = reasoning_effort
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
        max_tokens: int | None = None,
    ) -> str:
        """Generate text through the chat-completions compatible endpoint."""
        return await self._generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format=None,
            max_tokens=max_tokens,
        )

    async def generate_json_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Generate a JSON object when the configured provider supports JSON mode."""
        if not self.json_mode_enabled:
            raise ValueError("JSON mode is not enabled for this LLM provider")
        return await self._generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )

    async def generate_structured_json_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, object],
        schema_name: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Generate JSON constrained by a provider-supported JSON Schema."""
        if not self.json_mode_enabled:
            raise ValueError("JSON mode is not enabled for this LLM provider")
        if not schema_name.strip():
            raise ValueError("schema_name must not be empty")
        if max_tokens is not None and max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        return await self._generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                },
            },
            max_tokens=max_tokens,
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
        response_format: dict[str, object] | None,
        max_tokens: int | None,
    ) -> str:
        """Share retry and response parsing across text and JSON modes."""
        if max_tokens is not None and max_tokens < 1:
            raise ValueError("max_tokens must be positive")
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
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
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

    async def stream_with_tools(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.0,
        tool_choice: str = "auto",
    ) -> AsyncIterator[LLMToolStreamEvent]:
        """Stream a direct answer or return one assembled native Tool-call batch."""
        if not tools:
            raise ValueError("native Tool streaming requires at least one Tool")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("unsupported tool_choice")
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,
            "temperature": temperature,
            "stream": True,
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.circuit_breaker.before_call()
        async with self.concurrency_limiter.slot():
            try:
                if self.http_client is not None:
                    async for event in self._stream_tool_post(
                        self.http_client, payload, headers
                    ):
                        yield event
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        async for event in self._stream_tool_post(
                            client, payload, headers
                        ):
                            yield event
            except httpx.HTTPError as exc:
                self.circuit_breaker.record_failure()
                raise classify_httpx_error(exc) from exc
            except Exception:
                self.circuit_breaker.record_failure()
                raise
            else:
                self.circuit_breaker.record_success()

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        headers: dict[str, str],
        parser: Callable[[dict[str, object]], T],
    ) -> T:
        operation = _metric_operation(current_turn_stage())
        trace = _HTTPTraceTimings()
        request_started = perf_counter()
        record_turn_counter(f"llm_usage.{operation}.request_count", 1.0)
        try:
            request = client.build_request(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                extensions={"trace": trace},
            )
            response = await client.send(request)
        except httpx.HTTPError as exc:
            raise classify_httpx_error(exc) from exc
        finally:
            record_turn_stage(
                f"llm_http.{operation}.total",
                (perf_counter() - request_started) * 1000,
            )
            trace.flush(operation)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise classify_httpx_error(exc) from exc

        parse_started = perf_counter()
        try:
            data = response.json()
        except ValueError as exc:
            raise PermanentProviderError("LLM provider returned invalid JSON.") from exc
        finally:
            record_turn_stage(
                f"llm_http.{operation}.response_parse",
                (perf_counter() - parse_started) * 1000,
            )
        if not isinstance(data, dict):
            raise PermanentProviderError("LLM provider returned an invalid object.")
        _record_usage(data.get("usage"), operation=operation)
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

    async def _stream_tool_post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> AsyncIterator[LLMToolStreamEvent]:
        """Parse text deltas while assembling provider-split function calls."""
        received_content = False
        calls: dict[int, dict[str, str]] = {}
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
                content, fragments = _stream_tool_delta(data)
                if content:
                    if calls:
                        raise PermanentProviderError(
                            "LLM mixed text and Tool calls in one streamed turn."
                        )
                    received_content = True
                    yield LLMToolStreamEvent(
                        type="content_delta", content_delta=content
                    )
                for index, fragment in fragments:
                    if received_content:
                        raise PermanentProviderError(
                            "LLM mixed text and Tool calls in one streamed turn."
                        )
                    current = calls.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    for key in ("id", "name", "arguments"):
                        current[key] += fragment[key]
        if calls:
            assembled: list[LLMToolCall] = []
            for index in sorted(calls):
                item = calls[index]
                try:
                    arguments = json.loads(item["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise PermanentProviderError(
                        "LLM streamed Tool arguments were invalid JSON."
                    ) from exc
                if not isinstance(arguments, dict):
                    raise PermanentProviderError(
                        "LLM streamed Tool arguments must be an object."
                    )
                assembled.append(
                    LLMToolCall(
                        call_id=item["id"],
                        name=item["name"],
                        arguments=arguments,
                    )
                )
            yield LLMToolStreamEvent(type="tool_calls", tool_calls=assembled)
        elif not received_content:
            raise PermanentProviderError(
                "LLM streaming response included neither text nor Tool calls."
            )


def _text_response(message: dict[str, object]) -> str:
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise PermanentProviderError("LLM response did not include text content.")
    return content.strip()


def _metric_operation(stage: str | None) -> str:
    """Normalize an internal stage into a bounded metric path component."""
    if stage is None:
        return "unscoped"
    normalized = "".join(
        character if character.isalnum() or character in {"_", "."} else "_"
        for character in stage.casefold()
    )
    return normalized[:80] or "unscoped"


def _record_usage(raw_usage: object, *, operation: str) -> None:
    """Record standard and DeepSeek-compatible token counters when supplied."""
    if not isinstance(raw_usage, dict):
        mark_turn_flag(f"llm_usage_missing.{operation}")
        return
    mappings = {
        "prompt_tokens": "prompt_tokens",
        "completion_tokens": "completion_tokens",
        "total_tokens": "total_tokens",
        "prompt_cache_hit_tokens": "cached_prompt_tokens",
        "prompt_cache_miss_tokens": "uncached_prompt_tokens",
    }
    for provider_field, counter_name in mappings.items():
        value = _non_negative_number(raw_usage.get(provider_field))
        if value is not None:
            record_turn_counter(
                f"llm_usage.{operation}.{counter_name}",
                value,
            )
    prompt_details = raw_usage.get("prompt_tokens_details")
    if (
        isinstance(prompt_details, dict)
        and "prompt_cache_hit_tokens" not in raw_usage
    ):
        cached = _non_negative_number(prompt_details.get("cached_tokens"))
        if cached is not None:
            record_turn_counter(
                f"llm_usage.{operation}.cached_prompt_tokens",
                cached,
            )
    completion_details = raw_usage.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        reasoning = _non_negative_number(completion_details.get("reasoning_tokens"))
        if reasoning is not None:
            record_turn_counter(
                f"llm_usage.{operation}.reasoning_tokens",
                reasoning,
            )


def _non_negative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _http_trace_metric(event: str) -> str | None:
    """Map HTTP/1.1 and HTTP/2 trace events to stable phase names."""
    for suffix, metric in (
        ("connect_tcp", "connect"),
        ("start_tls", "tls"),
        ("send_request_headers", "request_headers"),
        ("send_request_body", "request_body"),
        ("receive_response_headers", "response_wait"),
        ("receive_response_body", "response_body"),
    ):
        if event.endswith(suffix):
            return metric
    return None


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


def _stream_tool_delta(
    data: str,
) -> tuple[str, list[tuple[int, dict[str, str]]]]:
    """Return content and validated partial Tool-call fields from one SSE event."""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise PermanentProviderError("LLM streaming event was invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise PermanentProviderError("LLM streaming event was not an object.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", []
    choice = choices[0]
    if not isinstance(choice, dict):
        raise PermanentProviderError("LLM streaming choice was invalid.")
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return "", []
    raw_content = delta.get("content")
    content = raw_content if isinstance(raw_content, str) else ""
    raw_calls = delta.get("tool_calls", [])
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise PermanentProviderError("LLM streamed tool_calls was not an array.")
    fragments: list[tuple[int, dict[str, str]]] = []
    for raw in raw_calls:
        if not isinstance(raw, dict) or not isinstance(raw.get("index"), int):
            raise PermanentProviderError("LLM streamed Tool call was invalid.")
        function = raw.get("function")
        if function is not None and not isinstance(function, dict):
            raise PermanentProviderError("LLM streamed function was invalid.")
        function = function or {}
        values = {
            "id": raw.get("id", ""),
            "name": function.get("name", ""),
            "arguments": function.get("arguments", ""),
        }
        if not all(isinstance(value, str) for value in values.values()):
            raise PermanentProviderError("LLM streamed Tool fields were invalid.")
        fragments.append((raw["index"], values))
    return content, fragments
