"""Tests for risk-aware blocking output checks and sentence streaming."""

from app.guardrails.policy import OutputGuardrailMode, OutputGuardrailPolicy
from app.models import Intent, RiskLevel


def test_low_risk_general_support_can_use_local_triage_streaming() -> None:
    decision = OutputGuardrailPolicy().decide(
        streaming_requested=True,
        risk_level=RiskLevel.LOW,
        intent=Intent.EMOTIONAL_SUPPORT,
        selected_skill="general_support_skill",
        memory_evidence=[],
    )

    assert decision.mode is OutputGuardrailMode.LOCAL_TRIAGE
    assert decision.sentence_streaming_allowed is True


def test_low_risk_general_support_can_use_local_triage_non_streaming() -> None:
    decision = OutputGuardrailPolicy().decide(
        streaming_requested=False,
        risk_level=RiskLevel.LOW,
        intent=Intent.EMOTIONAL_SUPPORT,
        selected_skill="general_support_skill",
        memory_evidence=[],
    )

    assert decision.mode is OutputGuardrailMode.LOCAL_TRIAGE
    assert decision.sentence_streaming_allowed is False
    assert decision.reason == "low_risk_general_support"


def test_memory_grounded_response_keeps_full_blocking_guardrail() -> None:
    decision = OutputGuardrailPolicy().decide(
        streaming_requested=True,
        risk_level=RiskLevel.LOW,
        intent=Intent.PROGRESS_REVIEW,
        selected_skill="general_support_skill",
        memory_evidence=["bounded evidence"],
    )

    assert decision.mode is OutputGuardrailMode.FULL
    assert decision.sentence_streaming_allowed is False
    assert decision.reason == "memory_grounded_response"


def test_any_tool_assisted_response_keeps_full_blocking_guardrail() -> None:
    decision = OutputGuardrailPolicy().decide(
        streaming_requested=True,
        risk_level=RiskLevel.LOW,
        intent=Intent.EMOTIONAL_SUPPORT,
        selected_skill="general_support_skill",
        memory_evidence=[],
        requires_full_guardrail=True,
    )

    assert decision.mode is OutputGuardrailMode.FULL
    assert decision.sentence_streaming_allowed is False
    assert decision.reason == "tool_assisted_response"


def test_resource_and_elevated_risk_responses_remain_final_only() -> None:
    policy = OutputGuardrailPolicy()

    resource = policy.decide(
        streaming_requested=True,
        risk_level=RiskLevel.LOW,
        intent=Intent.CAMPUS_RESOURCE_QUERY,
        selected_skill="support_resource_rag_skill",
        memory_evidence=[],
    )
    elevated = policy.decide(
        streaming_requested=True,
        risk_level=RiskLevel.MEDIUM,
        intent=Intent.EMOTIONAL_SUPPORT,
        selected_skill="general_support_skill",
        memory_evidence=[],
    )

    assert resource.mode is OutputGuardrailMode.FULL
    assert elevated.mode is OutputGuardrailMode.FULL


def test_non_streaming_sensitive_response_keeps_full_guardrail() -> None:
    decision = OutputGuardrailPolicy().decide(
        streaming_requested=False,
        risk_level=RiskLevel.LOW,
        intent=Intent.EMOTIONAL_SUPPORT,
        selected_skill="general_support_skill",
        memory_evidence=[],
        requires_full_guardrail=True,
    )

    assert decision.mode is OutputGuardrailMode.FULL
    assert decision.sentence_streaming_allowed is False
    assert decision.reason == "tool_assisted_response"
