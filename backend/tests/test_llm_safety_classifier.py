"""Tests for semantic safety enhancement and conservative hybrid merging."""

import pytest

from app.models import RiskLevel
from app.safety.classifier import HybridSafetyClassifier, LlmSafetyClassifier


class FakeLLMClient:
    """Async fake for safety-classifier responses."""

    def __init__(self, response: str, should_fail: bool = False) -> None:
        self.response = response
        self.should_fail = should_fail
        self.calls = 0

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("provider unavailable")
        return self.response


@pytest.mark.anyio
async def test_hybrid_safety_lets_llm_raise_subtle_risk() -> None:
    classifier = HybridSafetyClassifier(
        llm_classifier=LlmSafetyClassifier(
            FakeLLMClient('{"risk_level":"crisis","reason":"semantic concern"}')
        )
    )

    result = await classifier.classify("如果明天醒不过来也挺好")

    assert result.risk_level == RiskLevel.CRISIS
    assert result.llm_usage.used is True


@pytest.mark.anyio
async def test_hybrid_safety_never_downgrades_rule_result() -> None:
    classifier = HybridSafetyClassifier(
        llm_classifier=LlmSafetyClassifier(
            FakeLLMClient('{"risk_level":"low","reason":"under-called"}')
        )
    )

    result = await classifier.classify("我被威胁了，不知道该怎么办")

    assert result.risk_level == RiskLevel.HIGH
    assert result.llm_usage.used is True


@pytest.mark.anyio
async def test_hybrid_safety_skips_llm_after_rule_crisis() -> None:
    client = FakeLLMClient('{"risk_level":"low","reason":"wrong"}')
    classifier = HybridSafetyClassifier(llm_classifier=LlmSafetyClassifier(client))

    result = await classifier.classify("我不想活了")

    assert result.risk_level == RiskLevel.CRISIS
    assert client.calls == 0


@pytest.mark.anyio
async def test_hybrid_safety_falls_back_when_llm_fails() -> None:
    classifier = HybridSafetyClassifier(
        llm_classifier=LlmSafetyClassifier(FakeLLMClient("", should_fail=True))
    )

    result = await classifier.classify("我明天要小组讨论，有点紧张")

    assert result.risk_level == RiskLevel.LOW
    assert result.llm_usage.used is False
    assert result.llm_usage.fallback_used is True
    assert result.llm_usage.error_category == "TRANSIENT_PROVIDER_ERROR"
