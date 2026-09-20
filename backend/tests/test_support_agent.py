"""Deterministic tests for support generation and safe fallback behavior."""

import json

import pytest

from app.agents.support import SupportAgent
from app.agents.support_generation import (
    SupportGenerationAgent,
    requests_direct_wording_v2,
)
from app.llm.base import LLMToolCall, LLMToolResponse, LLMToolStreamEvent
from app.models import Intent, RiskLevel, SafetyResult
from app.models_memory_tool import MemoryToolEvidence, MemoryToolRunResult
from app.models_long_term_memory import MemoryType
from app.models_memory_correction import MemoryCorrectionAutoUpdateResult
from app.models_support_generation import PresentationConstraints, SupportGeneration
from app.tools.practice_memory import memory_tool_registry
from app.tools.runtime import ToolExecutionContext


class FakeLLMClient:
    """Return one configured support-generation response."""

    def __init__(self, response: str) -> None:
        self.response = response

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, user_prompt, temperature
        return self.response


class FakeJSONModeLLMClient(FakeLLMClient):
    """Expose JSON mode and fail if support generation uses plain text."""

    json_mode_enabled = True

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, user_prompt, temperature
        raise AssertionError("plain text generation must not be used")

    async def generate_json_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        del system_prompt, user_prompt, temperature
        return self.response


class FakeStreamingLLMClient(FakeLLMClient):
    """Yield configured provider deltas without buffering."""

    def __init__(self, deltas: list[str]) -> None:
        super().__init__("")
        self.deltas = deltas

    async def stream_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ):
        assert "纯文本" in system_prompt
        assert "User message" in user_prompt
        del temperature
        for delta in self.deltas:
            yield delta


class FakeIntegratedToolLLM:
    """Capture native Tool turns used by the main support generator."""

    def __init__(self, responses: list[LLMToolResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    async def complete_with_tools(self, **kwargs: object) -> LLMToolResponse:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class FakeIntegratedStreamingToolLLM(FakeIntegratedToolLLM):
    """Stream a direct main-model reply while exposing native Tools."""

    def __init__(
        self,
        events: list[LLMToolStreamEvent],
        responses: list[LLMToolResponse] | None = None,
    ) -> None:
        super().__init__(responses or [])
        self.events = events
        self.stream_requests: list[dict[str, object]] = []

    async def stream_text(self, **kwargs: object):
        del kwargs
        if False:
            yield ""

    async def stream_with_tools(self, **kwargs: object):
        self.stream_requests.append(kwargs)
        for event in self.events:
            yield event


class ReturningSearchService:
    """Return one bounded memory item and capture whether the Tool ran."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs: object) -> MemoryToolRunResult:
        self.calls.append(kwargs)
        return MemoryToolRunResult(
            decision_action="search_practice_memory",
            decision_reason="explicit_cross_session_history",
            outcome="returned",
            evidence=[
                MemoryToolEvidence(
                    evidence_id="mem_ev_0123456789abcdef",
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    safe_summary="先准备一句开场曾经有帮助。",
                    relevance_score=0.9,
                )
            ],
        )


class NoopUpdateService:
    async def update(self, **kwargs: object) -> MemoryCorrectionAutoUpdateResult:
        del kwargs
        return MemoryCorrectionAutoUpdateResult(status="updated")


def _tool_context(message: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        user_id="owner",
        current_request=message,
        skill_name="general_support_skill",
        risk_level=RiskLevel.LOW,
    )


def _append_to(items: list[str]):
    async def append(item: str) -> None:
        items.append(item)

    return append


@pytest.mark.anyio
async def test_support_generation_streams_only_complete_checked_sentences() -> None:
    agent = SupportGenerationAgent(
        llm_client=FakeStreamingLLMClient(
            ["听起来这件事让你有些紧", "张。可以先准备一句开场。"]
        )
    )
    chunks: list[str] = []

    response, metadata = await agent.respond_stream(
        message="最近小组讨论让我有点紧张",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="low"),
        chunk_sink=_append_to(chunks),
    )

    assert chunks == ["听起来这件事让你有些紧张。", "可以先准备一句开场。"]
    assert response == "".join(chunks)
    assert metadata["native_streaming_used"] is True
    assert metadata["fallback_used"] is False


@pytest.mark.anyio
async def test_support_generation_never_streams_unsafe_sentence() -> None:
    agent = SupportGenerationAgent(
        llm_client=FakeStreamingLLMClient(
            ["先慢一点。", "你患有社交焦虑症。"]
        )
    )
    chunks: list[str] = []

    response, metadata = await agent.respond_stream(
        message="最近小组讨论让我有点紧张",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="low"),
        chunk_sink=_append_to(chunks),
    )

    assert chunks[0] == "先慢一点。"
    assert "你患有" not in "".join(chunks)
    assert response == "".join(chunks)
    assert metadata["stream_interrupted"] is True


@pytest.mark.parametrize(
    "message",
    [
        "这次我只想准备一句很短的开场。",
        "明天小组讨论我有点紧张，想准备一句简短开场。",
        "请只根据现在这句话，给一个不超过二十字的开场。",
        "请帮我组织一句简短拒绝。",
    ],
)
def test_e10b_direct_wording_v2_accepts_explicit_phrase_requests(
    message: str,
) -> None:
    assert requests_direct_wording_v2(message)


@pytest.mark.parametrize(
    "message",
    [
        "我想练习开场。",
        "陪我做一次角色扮演。",
        "给我一些参与小组讨论的建议。",
        "我不知道该怎么办。",
    ],
)
def test_e10b_direct_wording_v2_rejects_practice_and_generic_support(
    message: str,
) -> None:
    assert not requests_direct_wording_v2(message)


def test_support_generation_agent_uses_v2_direct_wording_matcher_by_default() -> None:
    agent = SupportGenerationAgent(llm_client=None)

    assert agent.direct_wording_matcher("这次我只想准备一句很短的开场。")


def _valid_generation() -> str:
    return json.dumps(
        {
            "response_mode": "micro_cbt",
            "acknowledgement": "听起来你担心在小组讨论中开口后被负面评价。",
            "situation_summary": "准备在小组讨论中补充一个观点。",
            "automatic_thought": "一开口大家就会觉得我的观点很差",
            "fact_prediction_distinction": (
                "已经发生的事实是你还没有发言；大家一定会负面评价是尚未发生的预测。"
            ),
            "balanced_thought": "我可能会紧张，也可以先说一个不完整的小观点。",
            "suggested_phrase": "我补充一个比较小的点。",
            "practice_steps": ["先写下开场", "低声读一遍"],
            "followup_question": None,
            "pause_supported": True,
            "needs_real_support": False,
            "real_support_note": None,
        },
        ensure_ascii=False,
    )


def _valid_support_only_generation() -> str:
    return json.dumps(
        {
            "response_mode": "support_only",
            "acknowledgement": "可以先从当前这一步慢慢处理。",
            "pause_supported": True,
            "needs_real_support": False,
        },
        ensure_ascii=False,
    )


@pytest.mark.anyio
async def test_support_only_omits_empty_practice_steps_heading() -> None:
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(_valid_support_only_generation())
    )

    response, data = await agent.respond(
        message="今天有点紧张。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert "低强度步骤" not in response
    assert data["fallback_used"] is False


def test_support_generation_accepts_compact_provider_contract() -> None:
    proposal = SupportGeneration.model_validate(
        {"m": "support_only", "a": "可以先慢一点。", "p": [], "n": False, "v": []}
    )

    assert proposal.response_mode == "support_only"
    assert proposal.acknowledgement == "可以先慢一点。"


@pytest.mark.anyio
async def test_support_main_model_direct_reply_does_not_run_a_second_call() -> None:
    llm = FakeIntegratedToolLLM(
        [LLMToolResponse(content=_valid_support_only_generation())]
    )
    search = ReturningSearchService()
    agent = SupportGenerationAgent(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=memory_tool_registry(
            search, update_service=NoopUpdateService()
        ),
    )
    message = "今天小组讨论时有点紧张。"

    _, metadata = await agent.respond(
        message=message,
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="low"),
        tool_context=_tool_context(message),
    )

    assert len(llm.requests) == 1
    assert len(llm.requests[0]["tools"]) == 2  # type: ignore[arg-type]
    assert search.calls == []
    assert metadata["memory_tool"]["decision_action"] == "skip"


@pytest.mark.anyio
async def test_support_main_model_continues_after_memory_tool_result() -> None:
    llm = FakeIntegratedToolLLM(
        [
            LLMToolResponse(
                tool_calls=[
                    LLMToolCall(
                        call_id="call-memory-1",
                        name="search_practice_memory",
                        arguments={"query": "以前有效的开场方法"},
                    )
                ]
            ),
            LLMToolResponse(content=_valid_support_only_generation()),
        ]
    )
    search = ReturningSearchService()
    agent = SupportGenerationAgent(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=memory_tool_registry(search),
    )
    message = "我以前保存过什么有效的开场方法？"

    _, metadata = await agent.respond(
        message=message,
        intent=Intent.PROGRESS_REVIEW,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="low"),
        tool_context=_tool_context(message),
    )

    assert len(llm.requests) == 2
    assert len(search.calls) == 1
    assert metadata["memory_tool"]["outcome"] == "returned"
    assert metadata["memory_evidence_count"] == 1
    assert metadata["requires_full_output_guardrail"] is True


@pytest.mark.anyio
async def test_support_stream_main_model_receives_tools_without_preflight_call() -> None:
    llm = FakeIntegratedStreamingToolLLM(
        [
            LLMToolStreamEvent(
                type="content_delta",
                content_delta="可以先准备一句简短开场。",
            )
        ]
    )
    search = ReturningSearchService()
    agent = SupportGenerationAgent(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=memory_tool_registry(search),
    )
    chunks: list[str] = []
    message = "今天小组讨论时有点紧张。"

    response, metadata = await agent.respond_stream(
        message=message,
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="low"),
        chunk_sink=_append_to(chunks),
        tool_context=_tool_context(message),
    )

    assert response == "可以先准备一句简短开场。"
    assert chunks == [response]
    assert len(llm.stream_requests) == 1
    assert len(llm.stream_requests[0]["tools"]) == 1  # type: ignore[arg-type]
    assert llm.requests == []
    assert search.calls == []
    assert metadata["memory_tool"]["decision_action"] == "skip"


@pytest.mark.anyio
async def test_support_stream_buffers_tool_assisted_reply_for_full_guardrail() -> None:
    llm = FakeIntegratedStreamingToolLLM(
        [
            LLMToolStreamEvent(
                type="tool_calls",
                tool_calls=[
                    LLMToolCall(
                        call_id="call-memory-1",
                        name="search_practice_memory",
                        arguments={"query": "以前有效的开场方法"},
                    )
                ],
            )
        ],
        responses=[LLMToolResponse(content="记录显示，先准备一句开场曾经有帮助。")],
    )
    search = ReturningSearchService()
    agent = SupportGenerationAgent(
        llm_client=llm,  # type: ignore[arg-type]
        tool_registry=memory_tool_registry(search),
    )
    chunks: list[str] = []
    message = "我以前保存过什么有效的开场方法？"

    response, metadata = await agent.respond_stream(
        message=message,
        intent=Intent.PROGRESS_REVIEW,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="low"),
        chunk_sink=_append_to(chunks),
        tool_context=_tool_context(message),
    )

    assert chunks == []
    assert response == "记录显示，先准备一句开场曾经有帮助。"
    assert len(search.calls) == 1
    assert len(llm.requests) == 1
    assert llm.requests[0]["tool_choice"] == "none"
    assert metadata["native_streaming_used"] is False
    assert metadata["requires_full_output_guardrail"] is True
    assert metadata["memory_evidence_count"] == 1


@pytest.mark.anyio
async def test_support_generation_uses_validated_micro_cbt_structure() -> None:
    agent = SupportGenerationAgent(llm_client=FakeLLMClient(_valid_generation()))

    response, data = await agent.respond(
        message="小组讨论时我总觉得一开口大家就会觉得我的观点很差。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert "事实与预测" in response
    assert "我补充一个比较小的点" in response
    assert "可以随时暂停、退出或把步骤调小" in response
    assert data["response_mode"] == "micro_cbt"
    assert data["fallback_used"] is False
    assert data["citations"]


@pytest.mark.anyio
async def test_support_generation_uses_provider_json_mode_when_enabled() -> None:
    agent = SupportGenerationAgent(
        llm_client=FakeJSONModeLLMClient(_valid_generation())
    )

    _response, data = await agent.respond(
        message="小组讨论时我总觉得一开口大家就会觉得我的观点很差。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert data["fallback_used"] is False
    assert data["json_mode_used"] is True


@pytest.mark.anyio
async def test_support_generation_guardrail_falls_back_on_coercive_output() -> None:
    payload = json.loads(_valid_generation())
    payload["practice_steps"] = ["不能暂停，必须立刻完成最高难度练习"]
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    response, data = await agent.respond(
        message="我想做一个很小的练习。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert "不能暂停" not in response
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "OUTPUT_GUARDRAIL"
    assert data["llm_usage"]["error_category"] == "PROHIBITED_SUPPORT_CONTENT"


@pytest.mark.anyio
async def test_support_generation_rejects_historical_thought_as_current_span() -> None:
    """Keep CBT current-message grounding strict for the generic support route."""
    payload = json.loads(_valid_generation())
    payload["automatic_thought"] = "一开口大家就会觉得我的观点很差"
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    _response, data = await agent.respond(
        message="我上次课堂发言前在担心什么？",
        intent=Intent.PROGRESS_REVIEW,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
        memory_tool_evidence=[
            "practice_experience: 当时担心一开口大家就会觉得观点很差。"
        ],
    )

    assert data["fallback_reason"] == "OUTPUT_GUARDRAIL"
    assert data["llm_usage"]["error_category"] == (
        "UNGROUNDED_AUTOMATIC_THOUGHT"
    )


def test_deterministic_fallback_honors_roleplay_pause() -> None:
    response, data = SupportAgent().respond(
        message="任意未预设的新场景",
        intent=Intent.ROLEPLAY_PRACTICE,
        safety_result=SafetyResult(risk_level=RiskLevel.MEDIUM, reason="test"),
    )

    assert "只要说“暂停”" in response
    assert "可以随时暂停、退出或把步骤调小" in response
    assert data["action"] == "deterministic_support_fallback"


@pytest.mark.anyio
async def test_high_risk_support_skips_llm_and_foregrounds_real_support() -> None:
    agent = SupportGenerationAgent(llm_client=FakeLLMClient(_valid_generation()))

    response, data = await agent.respond(
        message="这件事让我压力很大。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.HIGH, reason="test"),
    )

    assert "现实支持" in response
    assert "不能做诊断" in response
    assert data["fallback_reason"] == "HIGH_RISK_DETERMINISTIC_SUPPORT"


@pytest.mark.anyio
async def test_support_generation_validates_semantic_privacy_candidates() -> None:
    payload = json.loads(_valid_generation())
    payload["suggested_phrase"] = "我想和室友张三商量一下关灯时间。"
    payload["automatic_thought"] = None
    payload["privacy_candidates"] = [
        {"text": "张三", "category": "third_party_identity"},
        {"text": "不存在的名字", "category": "third_party_identity"},
    ]
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    response, data = await agent.respond(
        message="我想和室友张三商量一下关灯时间。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert "张三" not in response
    assert "[redacted:third_party_identity]" in response
    assert "不存在的名字" not in response
    assert data["privacy_redaction"]["semantic_categories"] == [
        "third_party_identity"
    ]


@pytest.mark.anyio
async def test_direct_practice_returns_only_requested_short_sentence() -> None:
    payload = json.loads(_valid_generation())
    payload.update(
        {
            "response_mode": "direct_practice",
            "acknowledgement": None,
            "automatic_thought": None,
            "suggested_phrase": "最近我睡得比较早，我们可以商量一下晚上的关灯时间吗？",
            "practice_steps": [],
            "presentation_constraints": {
                "verbosity": "brief",
                "max_chars": 30,
                "output_format": "single_sentence",
                "requested_language": "zh",
            },
        }
    )
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    response, data = await agent.respond(
        message="帮我写一句话和室友商量关灯，不超过30字。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert len(response) <= 30
    assert "情境：" not in response
    assert "低强度步骤" not in response
    assert data["response_mode"] == "direct_practice"
    assert data["presentation_constraints"]["max_chars"] == 30


@pytest.mark.anyio
async def test_application_constraints_limit_support_steps_independently() -> None:
    payload = json.loads(_valid_generation())
    payload["practice_steps"] = ["第一步", "第二步", "第三步"]
    payload["automatic_thought"] = None
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    response, data = await agent.respond(
        message="给我一条建议，怎么更自然地参与小组讨论？",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
        application_constraints=PresentationConstraints(
            output_format="steps",
            item_count=1,
        ),
    )

    assert "1. 第一步" in response
    assert "第二步" not in response
    assert data["presentation_constraints"]["item_count"] == 1


@pytest.mark.anyio
async def test_clarify_returns_only_one_bounded_question() -> None:
    payload = json.loads(_valid_generation())
    payload.update(
        {
            "response_mode": "clarify",
            "acknowledgement": None,
            "automatic_thought": None,
            "suggested_phrase": None,
            "practice_steps": [],
            "followup_question": "你更希望先整理想法，还是练习一句开场白？",
        }
    )
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    response, data = await agent.respond(
        message="我不知道该怎么办。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert response == "你更希望先整理想法，还是练习一句开场白？"
    assert data["response_mode"] == "clarify"


@pytest.mark.anyio
async def test_direct_practice_rejects_mode_without_explicit_wording_request() -> None:
    payload = json.loads(_valid_generation())
    payload.update(
        {
            "response_mode": "direct_practice",
            "acknowledgement": None,
            "automatic_thought": None,
            "suggested_phrase": "我先说一个初步想法。",
            "practice_steps": [],
        }
    )
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    _response, data = await agent.respond(
        message="我想练习一次课堂发言。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert data["fallback_reason"] == "OUTPUT_GUARDRAIL"
    assert data["llm_usage"]["error_category"] == "MODE_SELECTION_GUARDRAIL"


@pytest.mark.anyio
async def test_direct_practice_allows_neutral_illustrative_time() -> None:
    payload = json.loads(_valid_generation())
    payload.update(
        {
            "response_mode": "direct_practice",
            "acknowledgement": None,
            "automatic_thought": None,
            "suggested_phrase": "我明天有早课，能不能十二点后小声一点？",
            "practice_steps": [],
        }
    )
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    response, data = await agent.respond(
        message="帮我写一句和室友沟通晚上声音问题的话。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert "十二点后" in response
    assert data["fallback_used"] is False


@pytest.mark.anyio
async def test_schema_failure_exposes_safe_field_diagnostics() -> None:
    payload = json.loads(_valid_generation())
    payload["response_mode"] = "very_short"
    agent = SupportGenerationAgent(
        llm_client=FakeLLMClient(json.dumps(payload, ensure_ascii=False))
    )

    _response, data = await agent.respond(
        message="小组讨论时我总觉得一开口大家就会觉得我的观点很差。",
        intent=Intent.EMOTIONAL_SUPPORT,
        safety_result=SafetyResult(risk_level=RiskLevel.LOW, reason="test"),
    )

    assert data["fallback_reason"] == "SCHEMA_VALIDATION_ERROR"
    assert data["validation_issues"] == [
        "response_mode:literal_error"
    ]
