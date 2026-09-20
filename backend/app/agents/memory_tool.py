"""Provider-native agent loop for bounded practice-memory Tools."""

from __future__ import annotations

from app.llm.base import ToolCallingLLMClient
from app.llm.prompts import (
    build_memory_tool_decision_user_prompt,
    build_memory_tool_native_system_prompt,
)
from app.models import RiskLevel
from app.models_memory_correction import MemoryCorrectionAutoUpdateResult
from app.models_conversation_context import ConversationPromptContext
from app.models_memory_tool import (
    MemoryToolDecisionReason,
    MemoryToolRunResult,
)
from app.observability.turn_latency import measure_turn_stage
from app.tools.practice_memory import (
    PracticeMemorySearchService,
    memory_tool_registry,
)
from app.tools.agent_loop import AgentToolLoop, AgentToolLoopOutcome
from app.tools.runtime import ToolCallStatus, ToolExecutionContext, ToolRegistry


class MemoryToolAgent:
    """Let a native Tool-capable model make one bounded memory decision."""

    def __init__(
        self,
        *,
        llm_client: ToolCallingLLMClient | None,
        search_service: PracticeMemorySearchService | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        if (search_service is None) == (tool_registry is None):
            raise ValueError(
                "provide exactly one of search_service or tool_registry"
            )
        if llm_client is not None and not isinstance(
            llm_client,
            ToolCallingLLMClient,
        ):
            raise TypeError("MemoryToolAgent requires native Tool Calling support")
        self._llm_client = llm_client
        if tool_registry is not None:
            self._tool_registry = tool_registry
        else:
            assert search_service is not None
            self._tool_registry = memory_tool_registry(search_service)

    async def run(
        self,
        *,
        user_id: str,
        message: str,
        conversation_context: ConversationPromptContext | None,
        skill_name: str,
        risk_level: RiskLevel,
    ) -> MemoryToolRunResult:
        """Execute zero or one native Tool call under application-owned scope."""
        if risk_level in {RiskLevel.HIGH, RiskLevel.CRISIS}:
            return MemoryToolRunResult(outcome="blocked_risk")
        if self._llm_client is None:
            return MemoryToolRunResult(outcome="skipped")
        with measure_turn_stage("tool_calling"):
            result = await self._run_native_tool_loop(
                user_id=user_id,
                message=message,
                conversation_context=conversation_context,
                skill_name=skill_name,
                risk_level=risk_level,
            )
        return result

    async def _run_native_tool_loop(
        self,
        *,
        user_id: str,
        message: str,
        conversation_context: ConversationPromptContext | None,
        skill_name: str,
        risk_level: RiskLevel,
    ) -> MemoryToolRunResult:
        """Adapt the generic native Tool loop to the Memory result contract."""
        client = self._llm_client
        assert client is not None
        messages: list[dict[str, object]] = [
            {
                "role": "system",
                "content": build_memory_tool_native_system_prompt(),
            },
            {
                "role": "user",
                "content": build_memory_tool_decision_user_prompt(
                    message=message,
                    conversation_context=(
                        conversation_context.model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                        if conversation_context is not None
                        else {}
                    ),
                ),
            },
        ]
        loop_result = await AgentToolLoop(
            llm_client=client,
            tool_registry=self._tool_registry,
            maximum_model_turns=2,
            maximum_tool_calls=1,
            maximum_calls_per_tool=1,
        ).run(
            messages=messages,
            context=ToolExecutionContext(
                user_id=user_id,
                current_request=message,
                skill_name=skill_name,
                risk_level=risk_level,
            ),
            temperature=0.0,
        )
        if loop_result.outcome is AgentToolLoopOutcome.PROVIDER_ERROR:
            return MemoryToolRunResult(outcome="provider_error")
        if loop_result.outcome is not AgentToolLoopOutcome.COMPLETED:
            return MemoryToolRunResult(outcome="invalid_model_output")
        if not loop_result.tool_results:
            return MemoryToolRunResult(
                decision_action="skip",
                decision_reason=MemoryToolDecisionReason.MEMORY_NOT_NEEDED,
                outcome="skipped",
            )
        if len(loop_result.tool_results) != 1:
            return MemoryToolRunResult(outcome="invalid_model_output")
        run_result = loop_result.tool_results[0]
        if (
            run_result.status is ToolCallStatus.COMPLETED
            and isinstance(run_result.output, MemoryToolRunResult)
        ):
            return run_result.output
        if (
            run_result.status is ToolCallStatus.COMPLETED
            and isinstance(run_result.output, MemoryCorrectionAutoUpdateResult)
        ):
            return memory_update_run_result(run_result.output)
        return MemoryToolRunResult(
            decision_action="search_practice_memory",
            decision_reason=MemoryToolDecisionReason.EXPLICIT_CROSS_SESSION_HISTORY,
            outcome="tool_error",
        )


def memory_update_run_result(
    result: MemoryCorrectionAutoUpdateResult,
) -> MemoryToolRunResult:
    """Project a mutation result into the existing content-free run contract."""
    outcome = result.status
    if outcome == "not_correction":
        outcome = "skipped"
    elif outcome in {"unavailable", "resolution_failed"}:
        outcome = "tool_error"
    return MemoryToolRunResult(
        decision_action="update_practice_memory",
        decision_reason=MemoryToolDecisionReason.EXPLICIT_MEMORY_CORRECTION,
        outcome=outcome,
    )
