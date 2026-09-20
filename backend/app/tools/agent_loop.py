"""Bounded provider-native loop for registered application Tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from app.llm.base import ToolCallingLLMClient
from app.observability.turn_latency import measure_turn_stage
from app.tools.runtime import (
    ToolCall,
    ToolCallResult,
    ToolExecutionContext,
    ToolRegistry,
)


class AgentToolLoopOutcome(str, Enum):
    """Content-free terminal outcomes for one provider-native Tool loop."""

    COMPLETED = "completed"
    PROVIDER_ERROR = "provider_error"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True)
class AgentToolLoopResult:
    """Final model content and typed Tool results from one bounded run."""

    outcome: AgentToolLoopOutcome
    final_content: str | None
    tool_results: tuple[ToolCallResult, ...]
    model_turn_count: int
    tool_call_count: int


class AgentToolLoop:
    """Run sequential native Tool calls without application-domain policy."""

    def __init__(
        self,
        *,
        llm_client: ToolCallingLLMClient,
        tool_registry: ToolRegistry,
        maximum_model_turns: int = 3,
        maximum_tool_calls: int = 2,
        maximum_calls_per_tool: int = 1,
    ) -> None:
        if maximum_model_turns < 1 or maximum_model_turns > 10:
            raise ValueError("maximum_model_turns must be between 1 and 10")
        if maximum_tool_calls < 1 or maximum_tool_calls > 10:
            raise ValueError("maximum_tool_calls must be between 1 and 10")
        if maximum_calls_per_tool < 1 or maximum_calls_per_tool > 3:
            raise ValueError("maximum_calls_per_tool must be between 1 and 3")
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._maximum_model_turns = maximum_model_turns
        self._maximum_tool_calls = maximum_tool_calls
        self._maximum_calls_per_tool = maximum_calls_per_tool

    async def run(
        self,
        *,
        messages: list[dict[str, object]],
        context: ToolExecutionContext,
        temperature: float = 0.0,
    ) -> AgentToolLoopResult:
        """Run until a final assistant response or one bounded failure."""
        conversation = [dict(message) for message in messages]
        tools = self._tool_registry.provider_tools()
        tool_run = self._tool_registry.new_run(
            maximum_calls=self._maximum_tool_calls,
            maximum_calls_per_tool=self._maximum_calls_per_tool,
        )
        tool_results: list[ToolCallResult] = []

        for model_turn in range(1, self._maximum_model_turns + 1):
            can_continue_after_tool = model_turn < self._maximum_model_turns
            can_call_tool = (
                can_continue_after_tool
                and len(tool_results) < self._maximum_tool_calls
            )
            try:
                with measure_turn_stage("tool_model"):
                    response = await self._llm_client.complete_with_tools(
                        messages=conversation,
                        tools=tools,
                        temperature=temperature,
                        tool_choice="auto" if can_call_tool else "none",
                    )
            except Exception:
                return AgentToolLoopResult(
                    outcome=AgentToolLoopOutcome.PROVIDER_ERROR,
                    final_content=None,
                    tool_results=tuple(tool_results),
                    model_turn_count=model_turn,
                    tool_call_count=len(tool_results),
                )

            if not response.tool_calls:
                return AgentToolLoopResult(
                    outcome=AgentToolLoopOutcome.COMPLETED,
                    final_content=response.content,
                    tool_results=tuple(tool_results),
                    model_turn_count=model_turn,
                    tool_call_count=len(tool_results),
                )
            if not can_call_tool:
                return AgentToolLoopResult(
                    outcome=AgentToolLoopOutcome.BUDGET_EXCEEDED,
                    final_content=None,
                    tool_results=tuple(tool_results),
                    model_turn_count=model_turn,
                    tool_call_count=len(tool_results),
                )
            if len(response.tool_calls) != 1:
                return AgentToolLoopResult(
                    outcome=AgentToolLoopOutcome.INVALID_MODEL_OUTPUT,
                    final_content=None,
                    tool_results=tuple(tool_results),
                    model_turn_count=model_turn,
                    tool_call_count=len(tool_results),
                )

            model_call = response.tool_calls[0]
            with measure_turn_stage("tool_execution"):
                run_result = await tool_run.execute(
                    call=ToolCall(
                        name=model_call.name,
                        arguments=model_call.arguments,
                        call_id=model_call.call_id,
                    ),
                    context=context,
                )
            tool_results.append(run_result)
            conversation.append(response.as_assistant_message())
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": model_call.call_id,
                    "content": tool_result_content(run_result),
                }
            )

        return AgentToolLoopResult(
            outcome=AgentToolLoopOutcome.BUDGET_EXCEEDED,
            final_content=None,
            tool_results=tuple(tool_results),
            model_turn_count=self._maximum_model_turns,
            tool_call_count=len(tool_results),
        )


def tool_result_content(result: ToolCallResult) -> str:
    """Serialize a validated Tool result for the provider continuation turn."""
    if result.output is not None:
        return result.output.model_dump_json(exclude_none=True)
    return json.dumps(
        {"outcome": result.status.value},
        ensure_ascii=False,
        separators=(",", ":"),
    )
