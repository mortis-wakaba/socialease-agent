"""Provider-agnostic LLM client interfaces and Tool-call responses."""

import json
from collections.abc import AsyncIterator
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class LLMToolCall(BaseModel):
    """One validated function call returned by a provider."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    arguments: dict[str, object]

    def as_assistant_tool_call(self) -> dict[str, object]:
        """Render the call for the next Chat Completions request."""
        return {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(
                    self.arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        }


class LLMToolResponse(BaseModel):
    """Assistant text and zero or more validated Tool calls."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list, max_length=10)

    def as_assistant_message(self) -> dict[str, object]:
        """Render the complete assistant turn for Tool-result continuation."""
        message: dict[str, object] = {
            "role": "assistant",
            "content": self.content,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                item.as_assistant_tool_call() for item in self.tool_calls
            ]
        return message


class LLMToolStreamEvent(BaseModel):
    """One provider-neutral text delta or completed native Tool-call batch."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["content_delta", "tool_calls"]
    content_delta: str | None = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list, max_length=10)


class BaseLLMClient(Protocol):
    """Minimal text-generation contract used by agent modules."""

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Generate one text response from system and user prompts."""
        ...


@runtime_checkable
class StreamingLLMClient(Protocol):
    """Optional provider capability for incremental plain-text generation."""

    def stream_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Yield non-empty text deltas in provider order."""
        ...


@runtime_checkable
class JSONModeLLMClient(Protocol):
    """Optional provider capability for guaranteed JSON-object syntax."""

    json_mode_enabled: bool

    async def generate_json_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Generate one syntactically valid JSON object as text."""
        ...


@runtime_checkable
class StructuredJSONLLMClient(Protocol):
    """Optional provider capability for schema-constrained JSON generation."""

    json_mode_enabled: bool

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
        """Generate JSON constrained by the supplied JSON Schema."""
        ...


@runtime_checkable
class ToolCallingLLMClient(Protocol):
    """Optional native function-calling capability exposed by providers."""

    async def complete_with_tools(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.0,
        tool_choice: str = "auto",
    ) -> LLMToolResponse:
        """Return assistant text or native function calls."""
        ...


@runtime_checkable
class StreamingToolCallingLLMClient(Protocol):
    """Optional native function-calling capability with streamed text replies."""

    def stream_with_tools(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.0,
        tool_choice: str = "auto",
    ) -> AsyncIterator[LLMToolStreamEvent]:
        """Yield text deltas or one completed Tool-call batch."""
        ...
