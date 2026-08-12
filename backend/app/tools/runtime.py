"""Provider-neutral Tool registry with bounded, validated execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import RiskLevel


class ToolExecutionContext(BaseModel):
    """Application-owned context that a model cannot supply or override."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    current_request: str = Field(min_length=1, max_length=20_000)
    skill_name: str = Field(min_length=1, max_length=128)
    risk_level: RiskLevel


class ToolSpec(BaseModel):
    """Portable function definition suitable for provider adapters."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    description: str = Field(min_length=1, max_length=500)
    input_schema: dict[str, object]

    def as_provider_tool(self) -> dict[str, object]:
        """Render the common OpenAI-compatible function Tool envelope."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
                "strict": True,
            },
        }


class AgentTool(Protocol):
    """One registered application Tool with typed arguments and output."""

    name: str
    description: str
    input_model: type[BaseModel]

    async def execute(
        self,
        *,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> BaseModel: ...


class ToolCall(BaseModel):
    """One provider-independent request to invoke a registered Tool."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    arguments: dict[str, object] = Field(default_factory=dict)
    call_id: str | None = Field(default=None, min_length=1, max_length=128)


class ToolCallStatus(str, Enum):
    """Content-free execution outcomes safe for traces."""

    COMPLETED = "completed"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    EXECUTION_ERROR = "execution_error"
    RUN_BUDGET_EXCEEDED = "run_budget_exceeded"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"


@dataclass(frozen=True)
class ToolCallResult:
    """Validated Tool output or one fail-closed error category."""

    name: str
    call_id: str | None
    status: ToolCallStatus
    output: BaseModel | None = None


class ToolRegistry:
    """Own unique Tool definitions and create isolated bounded runs."""

    def __init__(self, tools: list[AgentTool] | None = None) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        """Register one Tool and reject ambiguous duplicate names."""
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        ToolSpec(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_model.model_json_schema(),
        )
        self._tools[tool.name] = tool

    def specifications(self) -> list[ToolSpec]:
        """Return deterministic definitions without exposing executors."""
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_model.model_json_schema(),
            )
            for tool in self._tools.values()
        ]

    def provider_tools(self) -> list[dict[str, object]]:
        """Return definitions ready for an OpenAI-compatible adapter."""
        return [spec.as_provider_tool() for spec in self.specifications()]

    def new_run(
        self,
        *,
        maximum_calls: int = 3,
        maximum_calls_per_tool: int = 1,
    ) -> "BoundedToolRun":
        """Create per-agent-run counters; registries remain reusable."""
        return BoundedToolRun(
            registry=self,
            maximum_calls=maximum_calls,
            maximum_calls_per_tool=maximum_calls_per_tool,
        )

    def _resolve(self, name: str) -> AgentTool | None:
        return self._tools.get(name)


class BoundedToolRun:
    """Execute validated calls under total and per-Tool limits."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        maximum_calls: int,
        maximum_calls_per_tool: int,
    ) -> None:
        self._registry = registry
        self.maximum_calls = min(max(maximum_calls, 1), 10)
        self.maximum_calls_per_tool = min(max(maximum_calls_per_tool, 1), 3)
        self._call_count = 0
        self._calls_by_tool: dict[str, int] = {}

    async def execute(
        self,
        *,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolCallResult:
        """Validate model arguments and fail closed on all Tool exceptions."""
        if self._call_count >= self.maximum_calls:
            return _failure(call, ToolCallStatus.RUN_BUDGET_EXCEEDED)
        tool = self._registry._resolve(call.name)
        if tool is None:
            return _failure(call, ToolCallStatus.UNKNOWN_TOOL)
        if self._calls_by_tool.get(call.name, 0) >= self.maximum_calls_per_tool:
            return _failure(call, ToolCallStatus.TOOL_BUDGET_EXCEEDED)
        self._call_count += 1
        self._calls_by_tool[call.name] = self._calls_by_tool.get(call.name, 0) + 1
        try:
            arguments = tool.input_model.model_validate(call.arguments)
        except ValidationError:
            return _failure(call, ToolCallStatus.INVALID_ARGUMENTS)
        try:
            output = await tool.execute(arguments=arguments, context=context)
        except Exception:
            return _failure(call, ToolCallStatus.EXECUTION_ERROR)
        return ToolCallResult(
            name=call.name,
            call_id=call.call_id,
            status=ToolCallStatus.COMPLETED,
            output=output,
        )


def _failure(call: ToolCall, status: ToolCallStatus) -> ToolCallResult:
    return ToolCallResult(name=call.name, call_id=call.call_id, status=status)
