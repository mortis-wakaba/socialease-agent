"""Tests for risk-aware blocking output checks and sentence streaming."""

from app.guardrails.policy import OutputGuardrailMode, OutputGuardrailPolicy
from app.models import Intent, RiskLevel


def test_low_risk_general_support_can_use_deterministic_sentence_streaming() -> None:
    decision = OutputGuardrailPolicy().decide(
        streaming_requested=True,
        risk_level=RiskLevel.LOW,
        intent=Intent.EMOTIONAL_SUPPORT,
        selected_skill="general_support_skill",
        memory_evidence=[],
    )

    assert decision.mode is OutputGuardrailMode.DETERMINISTIC_ONLY
    assert decision.sentence_streaming_allowed is True


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
