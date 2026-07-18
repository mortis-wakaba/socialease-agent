"""Deterministic tests for support generation and safe fallback behavior."""

import json

import pytest

from app.agents.support import SupportAgent
from app.agents.support_generation import SupportGenerationAgent
from app.models import Intent, RiskLevel, SafetyResult
from app.models_support_generation import PresentationConstraints


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
    payload["presentation_constraints"] = {
        "verbosity": "very_short",
        "max_chars": None,
        "output_format": "plain",
        "requested_language": None,
    }
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
        "presentation_constraints.verbosity:literal_error"
    ]
