"""Contracts for the provider-neutral bounded Tool layer."""

from pydantic import BaseModel, ConfigDict, Field
import pytest

from app.models import RiskLevel
from app.models_memory_correction import MemoryCorrectionAutoUpdateResult
from app.models_memory_tool import MemoryToolRunResult
from app.tools.practice_memory import (
    SEARCH_PRACTICE_MEMORY_TOOL_NAME,
    UPDATE_PRACTICE_MEMORY_TOOL_NAME,
    memory_tool_registry,
)
from app.tools.runtime import (
    ToolCall,
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
    name = "echo_query"
    description = "Echo a validated query for Tool runtime tests."
    input_model = _Arguments

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def execute(self, *, arguments: BaseModel, context) -> BaseModel:
        del context
        self.calls += 1
        if self.fail:
            raise RuntimeError("demo failure")
        parsed = _Arguments.model_validate(arguments)
        return _Output(value=parsed.query)


class _CapturingMemorySearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs: object) -> MemoryToolRunResult:
        self.calls.append(kwargs)
        return MemoryToolRunResult(
            decision_action="search_practice_memory",
            decision_reason="explicit_cross_session_history",
            outcome="abstained",
        )


class _CapturingMemoryUpdate:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def update(self, **kwargs: object) -> MemoryCorrectionAutoUpdateResult:
        self.calls.append(kwargs)
        return MemoryCorrectionAutoUpdateResult(status="updated")


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        user_id="owner",
        current_request="我以前练过什么？",
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )


def test_registry_emits_provider_schema_without_server_owned_context() -> None:
    registry = ToolRegistry([_EchoTool()])

    definitions = registry.provider_tools()

    assert definitions[0]["type"] == "function"
    function = definitions[0]["function"]
    assert isinstance(function, dict)
    assert function["name"] == "echo_query"
    parameters = function["parameters"]
    assert isinstance(parameters, dict)
    assert set(parameters["properties"]) == {"query"}
    assert "user_id" not in str(parameters)
    assert parameters["additionalProperties"] is False


def test_registry_rejects_duplicate_tool_names() -> None:
    with pytest.raises(ValueError, match="duplicate tool name"):
        ToolRegistry([_EchoTool(), _EchoTool()])


@pytest.mark.anyio
async def test_bounded_run_validates_arguments_and_enforces_per_tool_limit() -> None:
    tool = _EchoTool()
    run = ToolRegistry([tool]).new_run(
        maximum_calls=3,
        maximum_calls_per_tool=1,
    )

    invalid = await run.execute(
        call=ToolCall(name="echo_query", arguments={"query": ""}),
        context=_context(),
    )
    repeated = await run.execute(
        call=ToolCall(name="echo_query", arguments={"query": "valid"}),
        context=_context(),
    )

    assert invalid.status is ToolCallStatus.INVALID_ARGUMENTS
    assert repeated.status is ToolCallStatus.TOOL_BUDGET_EXCEEDED
    assert tool.calls == 0


@pytest.mark.anyio
async def test_bounded_run_categorizes_unknown_and_execution_errors() -> None:
    run = ToolRegistry([_EchoTool(fail=True)]).new_run()

    unknown = await run.execute(
        call=ToolCall(name="unknown_tool", arguments={}),
        context=_context(),
    )
    failed = await run.execute(
        call=ToolCall(name="echo_query", arguments={"query": "valid"}),
        context=_context(),
    )

    assert unknown.status is ToolCallStatus.UNKNOWN_TOOL
    assert failed.status is ToolCallStatus.EXECUTION_ERROR
    assert failed.output is None


@pytest.mark.anyio
async def test_memory_tool_accepts_only_query_and_injects_application_scope() -> None:
    search = _CapturingMemorySearch()
    registry = memory_tool_registry(search)
    provider_schema = registry.provider_tools()[0]
    run = registry.new_run(maximum_calls=1)

    result = await run.execute(
        call=ToolCall(
            name=SEARCH_PRACTICE_MEMORY_TOOL_NAME,
            arguments={"query": "过去课堂发言练习"},
        ),
        context=_context(),
    )

    assert result.status is ToolCallStatus.COMPLETED
    assert isinstance(result.output, MemoryToolRunResult)
    assert len(search.calls) == 1
    assert search.calls[0] == {
        "user_id": "owner",
        "query": "过去课堂发言练习",
        "current_request": "我以前练过什么？",
        "skill_name": "general_support_skill",
        "risk_level": RiskLevel.LOW,
    }
    assert provider_schema["function"] == {
        "name": SEARCH_PRACTICE_MEMORY_TOOL_NAME,
        "description": (
            "Search the current user's saved social-practice history when the "
            "request explicitly needs cross-session evidence."
        ),
        "parameters": registry.specifications()[0].input_schema,
        "strict": True,
    }


@pytest.mark.anyio
async def test_memory_update_tool_accepts_no_model_owned_target_fields() -> None:
    search = _CapturingMemorySearch()
    update = _CapturingMemoryUpdate()
    registry = memory_tool_registry(search, update_service=update)
    definitions = registry.provider_tools()
    update_schema = definitions[1]["function"]
    run = registry.new_run(maximum_calls=1)

    invalid = await run.execute(
        call=ToolCall(
            name=UPDATE_PRACTICE_MEMORY_TOOL_NAME,
            arguments={"memory_id": "model-selected"},
        ),
        context=_context(),
    )

    assert invalid.status is ToolCallStatus.INVALID_ARGUMENTS
    assert update.calls == []
    assert update_schema["parameters"]["properties"] == {}

    result = await registry.new_run(maximum_calls=1).execute(
        call=ToolCall(
            name=UPDATE_PRACTICE_MEMORY_TOOL_NAME,
            arguments={},
        ),
        context=_context().model_copy(
            update={"current_request": "之前那条记错了，现在改成三个关键词。"}
        ),
    )

    assert result.status is ToolCallStatus.COMPLETED
    assert isinstance(result.output, MemoryCorrectionAutoUpdateResult)
    assert update.calls == [
        {
            "user_id": "owner",
            "statement": "之前那条记错了，现在改成三个关键词。",
        }
    ]
