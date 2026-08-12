"""Contracts for model-decided, read-only episodic memory search."""

from datetime import UTC, datetime
import json

import pytest

from app.agents.memory_evidence_selector import (
    MemoryEvidenceSelector,
    memory_evidence_selector_enabled,
)
from app.agents.memory_tool import MemoryToolAgent
from app.llm.base import LLMToolCall, LLMToolResponse
from app.models import Intent, IntentResult, RiskLevel, SafetyResult
from app.models_long_term_memory import (
    MemoryRecordStatus,
    MemoryRetrievalDiagnostics,
    MemoryRetrievalHit,
    MemoryRetrievalResult,
    MemoryRetrievalScore,
    MemoryRetrievalStrategy,
    MemoryType,
)
from app.models_memory_correction import MemoryCorrectionAutoUpdateResult
from app.models_memory_tool import (
    MemoryEvidenceSelectorResult,
    MemoryToolEvidence,
    MemoryToolRunResult,
)
from app.services.memory_search_service import MemorySearchService
from app.skills.base import SkillContext
from app.skills.support import SupportSkill
from app.workflow.context import RunContext
from app.tools.practice_memory import memory_tool_registry


NOW = datetime(2026, 8, 9, tzinfo=UTC)


def test_memory_evidence_selector_defaults_on_with_explicit_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOCIALEASE_MEMORY_EVIDENCE_SELECTOR_ENABLED", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    assert memory_evidence_selector_enabled() is True

    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    assert memory_evidence_selector_enabled() is True

    monkeypatch.setenv("SOCIALEASE_MEMORY_EVIDENCE_SELECTOR_ENABLED", "true")
    assert memory_evidence_selector_enabled() is True

    monkeypatch.setenv("SOCIALEASE_MEMORY_EVIDENCE_SELECTOR_ENABLED", "false")
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    assert memory_evidence_selector_enabled() is False


class FixedLLM:
    """Return one strict decision and capture its prompt."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.user_prompt = ""

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, temperature
        self.user_prompt = user_prompt
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)


class NativeToolLLM:
    """Return one native Tool call followed by a completion acknowledgement."""

    def __init__(self, first: LLMToolResponse) -> None:
        self.responses = [first, LLMToolResponse(content="completed")]
        self.requests: list[dict[str, object]] = []

    async def complete_with_tools(self, **kwargs: object) -> LLMToolResponse:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class CapturingRetriever:
    """Capture the server-owned retrieval scope and return configured hits."""

    def __init__(self, summaries: list[str] | None = None) -> None:
        self.summaries = summaries or ["过去练习中，先说明当前安排有帮助。"]
        self.requests = []

    async def retrieve(self, request):
        self.requests.append(request)
        hits = [
            MemoryRetrievalHit(
                memory_id=f"memory-{index}",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary=summary,
                status=MemoryRecordStatus.ACTIVE,
                occurred_at=NOW,
                score=MemoryRetrievalScore(
                    lexical=0.8,
                    scenario=0.0,
                    recency=0.5,
                    novelty=1.0,
                    confidence=0.9,
                    total=0.8,
                ),
                estimated_tokens=12,
            )
            for index, summary in enumerate(self.summaries, start=1)
        ]
        return MemoryRetrievalResult(
            hits=hits,
            diagnostics=MemoryRetrievalDiagnostics(
                strategy=MemoryRetrievalStrategy.HYBRID,
                candidate_count=len(hits),
                eligible_count=len(hits),
                returned_count=len(hits),
                estimated_tokens=12 * len(hits),
                token_budget=256,
                abstained=not hits,
                consent_allowed=True,
            ),
        )


class CapturingSearch:
    """Capture one tool execution without a database dependency."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs: object) -> MemoryToolRunResult:
        self.calls.append(kwargs)
        return MemoryToolRunResult(
            decision_action="search_practice_memory",
            decision_reason="explicit_cross_session_history",
            outcome="abstained",
        )


class CapturingUpdate:
    """Capture a provider-native autonomous memory correction."""

    def __init__(self, status: str = "updated") -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []

    async def update(self, **kwargs: object) -> MemoryCorrectionAutoUpdateResult:
        self.calls.append(kwargs)
        return MemoryCorrectionAutoUpdateResult(status=self.status)


class FixedEvidenceSelector:
    """Select a configured subset while capturing the bounded candidates."""

    def __init__(self, selected_indexes: list[int]) -> None:
        self.selected_indexes = selected_indexes
        self.candidates: list[MemoryToolEvidence] = []

    async def select(self, **kwargs: object) -> MemoryEvidenceSelectorResult:
        candidates = kwargs["candidates"]
        assert isinstance(candidates, list)
        self.candidates = candidates
        selected = [candidates[index] for index in self.selected_indexes]
        return MemoryEvidenceSelectorResult(
            outcome="selected" if selected else "abstained",
            candidate_count=len(candidates),
            evidence=selected,
        )


class ReturningMemoryTool:
    """Return one fixed item as if a model-approved tool call succeeded."""

    async def run(self, **kwargs: object) -> MemoryToolRunResult:
        del kwargs
        return MemoryToolRunResult(
            decision_action="search_practice_memory",
            decision_reason="explicit_cross_session_history",
            outcome="returned",
            evidence=[
                MemoryToolEvidence(
                    evidence_id="mem_ev_0123456789abcdef",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    safe_summary="过去练习中，先写一句开场有帮助。",
                    relevance_score=0.9,
                )
            ],
        )


class ReturningMemoryUpdateTool:
    """Return one successful autonomous update for support integration."""

    async def run(self, **kwargs: object) -> MemoryToolRunResult:
        del kwargs
        return MemoryToolRunResult(
            decision_action="update_practice_memory",
            decision_reason="explicit_memory_correction",
            outcome="updated",
        )


class CapturingGenerationAgent:
    """Capture the sole episodic evidence input used by support generation."""

    def __init__(self) -> None:
        self.memory_tool_evidence: list[str] = []

    async def respond(self, **kwargs: object):
        evidence = kwargs.get("memory_tool_evidence", [])
        assert isinstance(evidence, list)
        self.memory_tool_evidence = evidence
        return "可以先从一句简短开场开始。", {"agent": "test_support"}


class CapturingMemoryGroundingAgent:
    """Capture the typed branch selected after a successful Memory Tool call."""

    def __init__(self) -> None:
        self.current_request = ""
        self.evidence: list[str] = []

    async def respond(self, **kwargs: object):
        self.current_request = str(kwargs["current_request"])
        evidence = kwargs["evidence"]
        assert isinstance(evidence, list)
        self.evidence = evidence
        return "记录显示，先写一句开场有帮助。", {
            "agent": "memory_grounding_generation_agent"
        }


@pytest.mark.anyio
async def test_memory_search_service_reuses_retriever_with_server_owned_scope() -> None:
    retriever = CapturingRetriever()
    service = MemorySearchService(retriever)  # type: ignore[arg-type]

    result = await service.search(
        user_id="owner",
        query="过去拒绝临时任务时有效的表达",
        current_request="以前什么方法对我有帮助？",
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )

    assert result.outcome == "returned"
    assert result.prompt_evidence == [
        "helpful_strategy: 过去练习中，先说明当前安排有帮助。"
    ]
    request = retriever.requests[0]
    assert request.user_id == "owner"
    assert request.strategy is MemoryRetrievalStrategy.SQL_TEXT
    assert set(request.allowed_memory_types) == {
        MemoryType.HELPFUL_STRATEGY,
        MemoryType.PRACTICE_EXPERIENCE,
    }


@pytest.mark.anyio
async def test_memory_search_service_selector_can_return_multiple_without_filling_top_three() -> None:
    retriever = CapturingRetriever(
        summaries=[
            "过去练习中，先说明当前安排有帮助。",
            "另一次练习中，提出一个替代时间也有帮助。",
            "过去曾直接答应所有安排。",
        ]
    )
    selector = FixedEvidenceSelector([0, 1])
    service = MemorySearchService(  # type: ignore[arg-type]
        retriever,
        evidence_selector=selector,
    )

    result = await service.search(
        user_id="owner",
        query="过去拒绝临时任务时有效的表达",
        current_request="以前哪些方法对我有帮助？",
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )

    assert result.outcome == "returned"
    assert result.selector_outcome == "selected"
    assert result.selector_candidate_count == 3
    assert len(result.evidence) == 2
    assert len(selector.candidates) == 3


@pytest.mark.anyio
async def test_memory_search_service_leaves_semantic_conflict_to_set_selector() -> None:
    retriever = CapturingRetriever(
        summaries=[
            "课堂发言时逐字背完整稿。",
            "课堂发言时准备一句简短开场。",
        ]
    )
    selector = FixedEvidenceSelector([1])
    service = MemorySearchService(  # type: ignore[arg-type]
        retriever,
        evidence_selector=selector,
    )

    result = await service.search(
        user_id="owner",
        query="以前课堂发言的开场办法",
        current_request="课堂发言时我不再逐字背完整稿，请找别的开场办法。",
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )

    assert len(selector.candidates) == 2
    assert [item.safe_summary for item in result.evidence] == [
        "课堂发言时准备一句简短开场。"
    ]


@pytest.mark.anyio
async def test_memory_evidence_selector_preserves_retrieval_order_for_complementary_items() -> None:
    candidates = [
        MemoryToolEvidence(
            evidence_id=f"mem_ev_{index:016x}",
            memory_type=MemoryType.HELPFUL_STRATEGY,
            safe_summary=f"候选 {index}",
            relevance_score=0.8,
        )
        for index in range(1, 4)
    ]
    llm = FixedLLM(
        {
            "question_direction": "helped",
            "selected_evidence_ids": [
                candidates[1].evidence_id,
                candidates[0].evidence_id,
            ]
        }
    )

    result = await MemoryEvidenceSelector(llm_client=llm).select(
        historical_question="以前哪些方法有效？",
        current_request="我以前哪些方法有效？",
        candidates=candidates,
    )

    assert result.outcome == "selected"
    assert result.question_direction.value == "helped"
    assert [item.evidence_id for item in result.evidence] == [
        candidates[0].evidence_id,
        candidates[1].evidence_id,
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "question_direction": "helped",
            "selected_evidence_ids": ["mem_ev_ffffffffffffffff"],
        },
        {
            "question_direction": "helped",
            "selected_evidence_ids": [
                "mem_ev_0000000000000001",
                "mem_ev_0000000000000001",
            ]
        },
    ],
)
async def test_memory_evidence_selector_fails_closed_on_invalid_subset(payload: object) -> None:
    candidate = MemoryToolEvidence(
        evidence_id="mem_ev_0000000000000001",
        memory_type=MemoryType.HELPFUL_STRATEGY,
        safe_summary="过去练习中有帮助的方法。",
        relevance_score=0.8,
    )

    result = await MemoryEvidenceSelector(llm_client=FixedLLM(payload)).select(
        historical_question="以前哪些方法有效？",
        current_request="我以前哪些方法有效？",
        candidates=[candidate],
    )

    assert result.outcome == "invalid_model_output"
    assert result.evidence == []


@pytest.mark.anyio
async def test_memory_search_service_blocks_control_and_high_risk_before_retrieval() -> None:
    retriever = CapturingRetriever()
    service = MemorySearchService(retriever)  # type: ignore[arg-type]

    control = await service.search(
        user_id="owner",
        query="过去的练习",
        current_request="删除我的长期记忆",
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )
    high_risk = await service.search(
        user_id="owner",
        query="过去的练习",
        current_request="当前请求",
        skill_name="general_support_skill",
        risk_level=RiskLevel.HIGH,
    )

    assert control.outcome == "blocked_control_intent"
    assert high_risk.outcome == "blocked_risk"
    assert retriever.requests == []


def test_memory_tool_agent_rejects_clients_without_native_tools() -> None:
    search = CapturingSearch()
    with pytest.raises(TypeError, match="native Tool Calling"):
        MemoryToolAgent(
            llm_client=FixedLLM({"action": "skip"}),  # type: ignore[arg-type]
            search_service=search,
        )


@pytest.mark.anyio
async def test_memory_tool_agent_executes_native_tool_call_and_continuation() -> None:
    llm = NativeToolLLM(
        LLMToolResponse(
            tool_calls=[
                LLMToolCall(
                    call_id="call-memory-1",
                    name="search_practice_memory",
                    arguments={"query": "过去关于课堂发言的有效练习策略"},
                )
            ]
        )
    )
    search = CapturingSearch()
    agent = MemoryToolAgent(llm_client=llm, search_service=search)  # type: ignore[arg-type]

    result = await agent.run(
        user_id="owner",
        message="我以前练过哪些课堂发言方法？",
        conversation_context=None,
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )

    assert result.outcome == "abstained"
    assert result.call_protocol == "native_tools_v1"
    assert len(search.calls) == 1
    assert len(llm.requests) == 2
    first_tools = llm.requests[0]["tools"]
    assert isinstance(first_tools, list)
    function = first_tools[0]["function"]
    assert isinstance(function, dict)
    assert set(function["parameters"]["properties"]) == {"query"}
    assert llm.requests[0]["tool_choice"] == "auto"
    assert llm.requests[1]["tool_choice"] == "none"
    continuation_messages = llm.requests[1]["messages"]
    assert isinstance(continuation_messages, list)
    assert continuation_messages[-2]["role"] == "assistant"
    assert continuation_messages[-2]["tool_calls"][0]["id"] == "call-memory-1"
    assert continuation_messages[-1]["role"] == "tool"
    assert continuation_messages[-1]["tool_call_id"] == "call-memory-1"


@pytest.mark.anyio
async def test_memory_tool_agent_maps_conservative_native_update() -> None:
    llm = NativeToolLLM(
        LLMToolResponse(
            tool_calls=[
                LLMToolCall(
                    call_id="call-update-1",
                    name="update_practice_memory",
                    arguments={},
                )
            ]
        )
    )
    search = CapturingSearch()
    update = CapturingUpdate()
    agent = MemoryToolAgent(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=memory_tool_registry(search, update_service=update),
    )

    result = await agent.run(
        user_id="owner",
        message="之前完整逐字稿那条记错了，现在改成三个关键词。",
        conversation_context=None,
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )

    assert result.decision_action == "update_practice_memory"
    assert result.decision_reason.value == "explicit_memory_correction"
    assert result.outcome == "updated"
    assert result.call_protocol == "native_tools_v1"
    assert update.calls == [
        {
            "user_id": "owner",
            "statement": "之前完整逐字稿那条记错了，现在改成三个关键词。",
        }
    ]
    assert search.calls == []


@pytest.mark.anyio
async def test_memory_tool_agent_native_skip_does_not_execute_tool() -> None:
    llm = NativeToolLLM(
        LLMToolResponse(
            content="当前上下文已经足够。"
        )
    )
    search = CapturingSearch()
    agent = MemoryToolAgent(llm_client=llm, search_service=search)  # type: ignore[arg-type]

    result = await agent.run(
        user_id="owner",
        message="继续刚才那句",
        conversation_context=None,
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )

    assert result.outcome == "skipped"
    assert result.decision_reason.value == "memory_not_needed"
    assert search.calls == []
    assert len(llm.requests) == 1


@pytest.mark.anyio
async def test_support_skill_passes_only_tool_evidence_to_generation() -> None:
    generation = CapturingGenerationAgent()
    skill = SupportSkill(
        generation_agent=generation,  # type: ignore[arg-type]
        memory_tool_agent=ReturningMemoryTool(),  # type: ignore[arg-type]
    )
    run = RunContext(
        run_id="run-1",
        user_id="owner",
        session_id=None,
        message="我以前练过什么开场方法？",
        request_context={},
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
        intent_result=IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=1.0,
            reason="test",
        ),
    )

    result = await skill.run(SkillContext(run=run))

    assert generation.memory_tool_evidence == [
        "helpful_strategy: 过去练习中，先写一句开场有帮助。"
    ]
    assert result.memory_evidence == generation.memory_tool_evidence
    assert result.structured_data["memory_tool"] == {
        "decision_action": "search_practice_memory",
        "decision_reason": "explicit_cross_session_history",
        "outcome": "returned",
            "evidence_count": 1,
            "output_parser_version": "memory-tool-native-v1",
            "selector_outcome": "not_run",
            "selector_candidate_count": 0,
            "selector_question_direction": None,
            "selector_output_parser_version": (
                "memory-evidence-selector-json-v2"
            ),
            "call_protocol": "native_tools_v1",
            "native_call_protocol_version": "native-tools-v1",
        }


@pytest.mark.anyio
async def test_support_skill_acknowledges_successful_memory_update() -> None:
    generation = CapturingGenerationAgent()
    skill = SupportSkill(
        generation_agent=generation,  # type: ignore[arg-type]
        memory_tool_agent=ReturningMemoryUpdateTool(),  # type: ignore[arg-type]
    )
    run = RunContext(
        run_id="run-memory-update",
        user_id="owner",
        session_id=None,
        message="之前完整逐字稿那条错了，现在改成三个关键词。",
        request_context={},
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
        intent_result=IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=1.0,
            reason="test",
        ),
    )

    result = await skill.run(SkillContext(run=run))

    assert result.response == "好的，我已经用你现在的说法替代了对应的旧记忆。"
    assert result.selected_agent == "memory_update_tool"
    assert result.structured_data["memory_updated"] is True
    assert result.structured_data["memory_tool"]["outcome"] == "updated"
    assert generation.memory_tool_evidence == []


@pytest.mark.anyio
async def test_support_skill_uses_typed_grounding_when_memory_tool_returns_evidence() -> None:
    generation = CapturingGenerationAgent()
    grounding = CapturingMemoryGroundingAgent()
    skill = SupportSkill(
        generation_agent=generation,  # type: ignore[arg-type]
        memory_tool_agent=ReturningMemoryTool(),  # type: ignore[arg-type]
        memory_grounding_agent=grounding,  # type: ignore[arg-type]
    )
    run = RunContext(
        run_id="run-grounding",
        user_id="owner",
        session_id=None,
        message="我以前练过什么开场方法？",
        request_context={},
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
        intent_result=IntentResult(
            intent=Intent.PROGRESS_REVIEW,
            confidence=1.0,
            reason="test",
        ),
    )

    result = await skill.run(SkillContext(run=run))

    assert grounding.current_request == run.message
    assert grounding.evidence == [
        "helpful_strategy: 过去练习中，先写一句开场有帮助。"
    ]
    assert generation.memory_tool_evidence == []
    assert result.selected_agent == "memory_grounding_generation_agent"
