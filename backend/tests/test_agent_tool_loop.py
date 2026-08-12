"""Behavioral tests for the bounded provider-native Agent Tool loop."""

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.llm.base import LLMToolCall, LLMToolResponse
from app.models import RiskLevel
from app.tools.agent_loop import AgentToolLoop, AgentToolLoopOutcome
from app.tools.runtime import (
    ToolCallStatus,
    ToolExecutionContext,
    ToolRegistry,
)


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2)


class _Output(BaseModel):
    value: str


class _EchoTool:
    description = "Echo a validated query for Agent Tool loop tests."
    input_model = _Arguments

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    async def execute(self, *, arguments: BaseModel, context) -> BaseModel:
        del context
        parsed = _Arguments.model_validate(arguments)
        self.calls.append(parsed.query)
        return _Output(value=parsed.query)


class _SequenceToolLLM:
    def __init__(self, responses: list[LLMToolResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    async def complete_with_tools(self, **kwargs: object) -> LLMToolResponse:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class _FailingToolLLM:
    async def complete_with_tools(self, **kwargs: object) -> LLMToolResponse:
        del kwargs
        raise RuntimeError("provider unavailable")


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        user_id="owner",
        current_request="查找相关内容",
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )


def _messages() -> list[dict[str, object]]:
    return [
        {"role": "system", "content": "Use Tools only when needed."},
        {"role": "user", "content": "查找相关内容"},
    ]


def _call(call_id: str, name: str, query: str) -> LLMToolResponse:
    return LLMToolResponse(
        tool_calls=[
            LLMToolCall(
                call_id=call_id,
                name=name,
                arguments={"query": query},
            )
        ]
    )


@pytest.mark.anyio
async def test_agent_tool_loop_returns_direct_completion_without_tool() -> None:
    tool = _EchoTool("first_tool")
    llm = _SequenceToolLLM([LLMToolResponse(content="direct answer")])

    result = await AgentToolLoop(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=ToolRegistry([tool]),
        maximum_model_turns=2,
        maximum_tool_calls=1,
    ).run(messages=_messages(), context=_context())

    assert result.outcome is AgentToolLoopOutcome.COMPLETED
    assert result.final_content == "direct answer"
    assert result.tool_results == ()
    assert result.model_turn_count == 1
    assert result.tool_call_count == 0
    assert tool.calls == []
    assert llm.requests[0]["tool_choice"] == "auto"


@pytest.mark.anyio
async def test_agent_tool_loop_supports_sequential_distinct_tools() -> None:
    first = _EchoTool("first_tool")
    second = _EchoTool("second_tool")
    llm = _SequenceToolLLM(
        [
            _call("call-1", "first_tool", "first query"),
            _call("call-2", "second_tool", "second query"),
            LLMToolResponse(content="final answer"),
        ]
    )

    result = await AgentToolLoop(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=ToolRegistry([first, second]),
        maximum_model_turns=3,
        maximum_tool_calls=2,
        maximum_calls_per_tool=1,
    ).run(messages=_messages(), context=_context())

    assert result.outcome is AgentToolLoopOutcome.COMPLETED
    assert result.final_content == "final answer"
    assert result.model_turn_count == 3
    assert result.tool_call_count == 2
    assert [item.status for item in result.tool_results] == [
        ToolCallStatus.COMPLETED,
        ToolCallStatus.COMPLETED,
    ]
    assert first.calls == ["first query"]
    assert second.calls == ["second query"]
    assert [request["tool_choice"] for request in llm.requests] == [
        "auto",
        "auto",
        "none",
    ]
    continuation = llm.requests[2]["messages"]
    assert isinstance(continuation, list)
    assert [message["role"] for message in continuation[-4:]] == [
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert continuation[-1]["tool_call_id"] == "call-2"


@pytest.mark.anyio
async def test_agent_tool_loop_rejects_parallel_calls_without_execution() -> None:
    first = _EchoTool("first_tool")
    llm = _SequenceToolLLM(
        [
            LLMToolResponse(
                tool_calls=[
                    LLMToolCall(
                        call_id="call-1",
                        name="first_tool",
                        arguments={"query": "first query"},
                    ),
                    LLMToolCall(
                        call_id="call-2",
                        name="first_tool",
                        arguments={"query": "second query"},
                    ),
                ]
            )
        ]
    )

    result = await AgentToolLoop(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=ToolRegistry([first]),
        maximum_model_turns=2,
        maximum_tool_calls=1,
    ).run(messages=_messages(), context=_context())

    assert result.outcome is AgentToolLoopOutcome.INVALID_MODEL_OUTPUT
    assert result.tool_call_count == 0
    assert first.calls == []


@pytest.mark.anyio
async def test_agent_tool_loop_rejects_call_after_budget_is_exhausted() -> None:
    first = _EchoTool("first_tool")
    llm = _SequenceToolLLM(
        [
            _call("call-1", "first_tool", "first query"),
            _call("call-2", "first_tool", "second query"),
        ]
    )

    result = await AgentToolLoop(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=ToolRegistry([first]),
        maximum_model_turns=2,
        maximum_tool_calls=1,
    ).run(messages=_messages(), context=_context())

    assert result.outcome is AgentToolLoopOutcome.BUDGET_EXCEEDED
    assert result.tool_call_count == 1
    assert first.calls == ["first query"]
    assert llm.requests[1]["tool_choice"] == "none"


@pytest.mark.anyio
async def test_agent_tool_loop_categorizes_provider_failure() -> None:
    result = await AgentToolLoop(
        llm_client=_FailingToolLLM(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry([_EchoTool("first_tool")]),
        maximum_model_turns=2,
        maximum_tool_calls=1,
    ).run(messages=_messages(), context=_context())

    assert result.outcome is AgentToolLoopOutcome.PROVIDER_ERROR
    assert result.model_turn_count == 1
    assert result.tool_call_count == 0
