"""Tests for default LLM-first intent routing with deterministic fallback."""

import pytest

from app.models import Intent, RiskLevel, SafetyResult
from app.workflow.router import LlmIntentRouter


class FakeLLMClient:
    """Async fake for LLM router outputs."""

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


@pytest.fixture
def low_safety() -> SafetyResult:
    """Return a reusable low-risk safety fixture."""
    return SafetyResult(risk_level=RiskLevel.LOW, reason="safe")


@pytest.mark.anyio
async def test_llm_router_is_default_when_available(low_safety: SafetyResult) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            '{"intent":"exposure_planning","confidence":0.91,"reason":"semantic match"}'
        )
    )

    result = await router.route("我想先一点点练，不想一下子就上台", low_safety)

    assert result.intent == Intent.EXPOSURE_PLANNING
    assert result.llm_usage.used is True
    assert result.llm_usage.fallback_used is False


@pytest.mark.anyio
async def test_llm_router_falls_back_on_invalid_output(low_safety: SafetyResult) -> None:
    router = LlmIntentRouter(llm_client=FakeLLMClient("not-json"))

    result = await router.route("我想模拟向老师提问的对话", low_safety)

    assert result.intent == Intent.ROLEPLAY_PRACTICE
    assert result.llm_usage.used is False
    assert result.llm_usage.fallback_used is True
    assert result.llm_usage.error_category == "INVALID_JSON"


@pytest.mark.anyio
async def test_llm_router_falls_back_on_provider_failure(low_safety: SafetyResult) -> None:
    router = LlmIntentRouter(llm_client=FakeLLMClient("", should_fail=True))

    result = await router.route("帮我做 thought record worksheet", low_safety)

    assert result.intent == Intent.CBT_WORKSHEET
    assert result.llm_usage.fallback_used is True
    assert result.llm_usage.error_category == "TRANSIENT_PROVIDER_ERROR"


@pytest.mark.anyio
async def test_crisis_skips_llm_router_call() -> None:
    client = FakeLLMClient(
        '{"intent":"roleplay_practice","confidence":0.9,"reason":"wrong"}'
    )
    router = LlmIntentRouter(llm_client=client)
    safety = SafetyResult(risk_level=RiskLevel.CRISIS, reason="crisis")

    result = await router.route("我想角色扮演", safety)

    assert result.intent == Intent.CRISIS
    assert client.calls == 0
    assert result.llm_usage.used is False


@pytest.mark.anyio
async def test_llm_router_low_confidence_action_requests_clarification(
    low_safety: SafetyResult,
) -> None:
    router = LlmIntentRouter(
        llm_client=FakeLLMClient(
            '{"intent":"roleplay_practice","confidence":0.41,"reason":"unclear"}'
        )
    )

    result = await router.route("你能帮我一下吗", low_safety)

    assert result.intent == Intent.CLARIFICATION_NEEDED
    assert result.llm_usage.used is True
