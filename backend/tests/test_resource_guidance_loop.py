"""Tests for the bounded read-only resource-guidance agent loop."""

from collections.abc import Iterable

import pytest

from app.agents.resource_guidance import ResourceGuidanceAgentLoop
from app.knowledge.service import KnowledgeService
from app.db.repositories import InMemoryTraceRepository
from app.models import ChatRequest, Intent, IntentResult, RiskLevel, SafetyResult
from app.models_resource_loop import ResourceLoopStopReason
from app.services.support_resource_service import SupportResourceService
from app.safety.classifier import RuleBasedSafetyClassifier
from app.skills.base import SkillContext
from app.skills.registry import SkillRegistry
from app.skills.support_rag import SupportRagSkill
from app.tracing.logger import TraceLogger
from app.workflow.context import RunContext
from app.workflow.engine import AgentHarness


class SequenceLLMClient:
    """Return deterministic model decisions in sequence."""

    def __init__(
        self,
        responses: Iterable[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.responses = iter(responses)
        self.error = error
        self.calls = 0

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        self.calls += 1
        assert "search_support_resources" in system_prompt
        assert "Original user query" in user_prompt
        assert temperature == 0.0
        if self.error is not None:
            raise self.error
        return next(self.responses)


class FailingKnowledgeService:
    """Raise on every tool execution for recovery coverage."""

    def query(self, query: str, kb_type: object) -> object:
        raise RuntimeError("retrieval unavailable")


class ResourceIntentRouter:
    """Route every safe test request to the resource skill."""

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        return IntentResult(
            intent=Intent.CAMPUS_RESOURCE_QUERY,
            confidence=1.0,
            reason="resource-loop-test",
        )


def _loop(
    client: SequenceLLMClient | None,
    *,
    knowledge: object | None = None,
) -> ResourceGuidanceAgentLoop:
    fallback_service = SupportResourceService()
    return ResourceGuidanceAgentLoop(
        llm_client=client,
        knowledge=knowledge or KnowledgeService(),  # type: ignore[arg-type]
        fallback_service=fallback_service,
        max_steps=3,
    )


@pytest.mark.anyio
async def test_resource_loop_uses_two_tools_then_finishes_grounded() -> None:
    client = SequenceLLMClient(
        [
            '{"action":"search_support_resources","reason":"先找公开资源",'
            '"query":"social anxiety CBT self-help public resource","observation_ids":[]}',
            '{"action":"search_practice_guidance","reason":"补充练习建议",'
            '"query":"课堂发言 开场句 社交练习","observation_ids":[]}',
            '{"action":"finish","reason":"已有资源和练习依据",'
            '"query":null,"observation_ids":[1,2]}',
        ]
    )

    result = await _loop(client).run("学校支持资源和课堂发言练习怎么找？")

    assert result.stop_reason == ResourceLoopStopReason.FINISHED
    assert result.used_agent_loop is True
    assert result.fallback_used is False
    assert result.llm_usage.used is True
    assert client.calls == 3
    assert len(result.steps) == 3
    assert result.steps[-1].outcome == "finished"
    assert result.citations
    assert "公开支持资源：" in result.answer
    assert "社交练习指导：" in result.answer


@pytest.mark.anyio
async def test_resource_loop_keeps_deterministic_mode_when_llm_disabled() -> None:
    result = await _loop(None).run("social anxiety CBT self-help public resource")

    assert result.stop_reason == ResourceLoopStopReason.LLM_DISABLED
    assert result.used_agent_loop is False
    assert result.fallback_used is False
    assert result.llm_usage.used is False
    assert result.citations


@pytest.mark.anyio
async def test_resource_loop_falls_back_on_invalid_or_unauthorized_action() -> None:
    client = SequenceLLMClient(
        [
            '{"action":"write_memory","reason":"try unsafe tool",'
            '"query":"secret","observation_ids":[]}'
        ]
    )

    result = await _loop(client).run("social anxiety CBT self-help public resource")

    assert result.stop_reason == ResourceLoopStopReason.INVALID_MODEL_OUTPUT
    assert result.fallback_used is True
    assert result.llm_usage.error_category == "INVALID_JSON"
    assert result.citations


@pytest.mark.anyio
async def test_resource_loop_rejects_finish_without_support_observation() -> None:
    client = SequenceLLMClient(
        [
            '{"action":"search_practice_guidance","reason":"先找练习",'
            '"query":"课堂发言 开场句","observation_ids":[]}',
            '{"action":"finish","reason":"直接完成",'
            '"query":null,"observation_ids":[1]}',
            '{"action":"search_support_resources","reason":"补充公开资源",'
            '"query":"social anxiety public resource","observation_ids":[]}',
        ]
    )

    result = await _loop(client).run("我想找资源并练习课堂发言")

    assert result.stop_reason == ResourceLoopStopReason.MAX_STEPS
    assert result.fallback_used is True
    assert result.steps[1].outcome == "rejected_missing_support_resource"
    assert result.llm_usage.error_category == "MAX_STEPS"


@pytest.mark.anyio
async def test_resource_loop_falls_back_when_step_budget_is_exhausted() -> None:
    search = (
        '{"action":"search_support_resources","reason":"继续检索",'
        '"query":"social anxiety public resource","observation_ids":[]}'
    )
    client = SequenceLLMClient([search, search, search])

    result = await _loop(client).run("社会支持资源")

    assert result.stop_reason == ResourceLoopStopReason.MAX_STEPS
    assert result.fallback_used is True
    assert len(result.steps) == 3
    assert client.calls == 3


@pytest.mark.anyio
async def test_resource_loop_falls_back_on_provider_or_tool_failure() -> None:
    provider_result = await _loop(
        SequenceLLMClient([], error=RuntimeError("provider unavailable"))
    ).run("social anxiety CBT self-help public resource")
    tool_result = await _loop(
        SequenceLLMClient(
            [
                '{"action":"search_support_resources","reason":"查找资源",'
                '"query":"public support resource","observation_ids":[]}'
            ]
        ),
        knowledge=FailingKnowledgeService(),
    ).run("social anxiety CBT self-help public resource")

    assert provider_result.stop_reason == ResourceLoopStopReason.PROVIDER_ERROR
    assert provider_result.fallback_used is True
    assert tool_result.stop_reason == ResourceLoopStopReason.TOOL_ERROR
    assert tool_result.fallback_used is True


@pytest.mark.anyio
async def test_support_skill_exposes_trace_safe_loop_steps() -> None:
    client = SequenceLLMClient(
        [
            '{"action":"search_support_resources","reason":"手机号 13912345678 需要查资源",'
            '"query":"social anxiety public resource","observation_ids":[]}',
            '{"action":"finish","reason":"资源足够",'
            '"query":null,"observation_ids":[1]}',
        ]
    )
    agent_loop = _loop(client)
    skill = SupportRagSkill(agent_loop=agent_loop)
    context = SkillContext(
        run=RunContext(
            run_id="run-resource-loop",
            user_id="demo_user",
            session_id=None,
            message="我想找公开支持资源",
            request_context={},
            safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="safe"),
            intent_result=IntentResult(
                intent=Intent.CAMPUS_RESOURCE_QUERY,
                confidence=0.9,
                reason="resource query",
            ),
        )
    )

    result = await skill.run(context)

    assert result.selected_agent == "support_resource_rag_agent"
    assert result.structured_data["agent_loop_used"] is True
    assert result.structured_data["agent_loop_stop_reason"] == "FINISHED"
    assert len(result.structured_data["agent_loop_steps"]) == 2
    assert "answer" not in result.structured_data["agent_loop_steps"][0]
    assert "13912345678" not in result.structured_data["agent_loop_steps"][0]["reason"]


@pytest.mark.anyio
async def test_agent_harness_persists_resource_loop_steps_in_run_trace() -> None:
    client = SequenceLLMClient(
        [
            '{"action":"search_support_resources","reason":"查找公开资源",'
            '"query":"social anxiety public resource","observation_ids":[]}',
            '{"action":"finish","reason":"资源足够",'
            '"query":null,"observation_ids":[1]}',
        ]
    )
    skill = SupportRagSkill(agent_loop=_loop(client))
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        safety_classifier=RuleBasedSafetyClassifier(),
        intent_router=ResourceIntentRouter(),
        skill_registry=SkillRegistry(executable_skills=[skill]),
    )

    response = await harness.run(
        ChatRequest(
            user_id="resource_loop_trace_user",
            message="我想找公开支持资源",
        )
    )

    assert response.trace.agent_loop_used is True
    assert response.trace.agent_loop_stop_reason == "FINISHED"
    assert len(response.trace.agent_loop_steps) == 2
    assert response.trace.agent_loop_steps[0]["action"] == "search_support_resources"
    grounding = response.structured_data["grounding_metadata"]
    assert grounding["retrieval_unknown"] is False
    assert grounding["citation_count"] >= 1
    assert grounding["citation_titles"]
    assert response.trace.output_guardrail_action == "allow"
