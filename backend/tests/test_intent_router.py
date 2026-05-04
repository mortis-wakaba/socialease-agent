"""Tests for the MVP intent router."""

import pytest

from app.models import Intent, RiskLevel, SafetyResult
from app.workflow.router import IntentRouter


@pytest.fixture
def low_safety() -> SafetyResult:
    """Return a reusable low-risk safety result for router tests."""
    return SafetyResult(risk_level=RiskLevel.LOW, reason="safe")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我想模拟向老师提问的对话", Intent.ROLEPLAY_PRACTICE),
        ("帮我做一次宿舍沟通角色扮演", Intent.ROLEPLAY_PRACTICE),
        ("我想练习拒绝别人请求", Intent.ROLEPLAY_PRACTICE),
        ("帮我做一个 CBT 自动想法记录", Intent.CBT_WORKSHEET),
        ("我想整理证据支持和证据反对，再写替代想法", Intent.CBT_WORKSHEET),
        ("帮我做 thought record worksheet", Intent.CBT_WORKSHEET),
        ("我想做一个由易到难的暴露练习计划", Intent.EXPOSURE_PLANNING),
        ("帮我设计社交焦虑阶梯和焦虑等级", Intent.EXPOSURE_PLANNING),
        ("学校心理中心在哪里，怎么预约咨询", Intent.CAMPUS_RESOURCE_QUERY),
        ("我可以找辅导员或者校内资源吗", Intent.CAMPUS_RESOURCE_QUERY),
        ("帮我复盘最近练习记录和进度", Intent.PROGRESS_REVIEW),
        ("今天只是有点难受，想说说", Intent.EMOTIONAL_SUPPORT),
    ],
)
def test_router_rule_based_cases(
    message: str,
    expected: Intent,
    low_safety: SafetyResult,
) -> None:
    router = IntentRouter()

    result = router.route(message, low_safety)

    assert result.intent == expected
    assert result.reason


def test_router_keyword_scoring_prefers_more_specific_intent(
    low_safety: SafetyResult,
) -> None:
    router = IntentRouter()

    result = router.route(
        "我想模拟面试自我介绍，也想练习对话和角色扮演",
        low_safety,
    )

    assert result.intent == Intent.ROLEPLAY_PRACTICE
    assert result.confidence > 0.7


def test_router_preserves_crisis_from_safety() -> None:
    router = IntentRouter()
    safety = SafetyResult(risk_level=RiskLevel.CRISIS, reason="crisis")

    result = router.route("我想角色扮演", safety)

    assert result.intent == Intent.CRISIS
    assert result.confidence == 1.0


def test_router_defaults_to_emotional_support_for_unknown_safe_message() -> None:
    router = IntentRouter()
    safety = SafetyResult(risk_level=RiskLevel.LOW, reason="safe")

    result = router.route("最近有点烦，但还没想好要做什么", safety)

    assert result.intent == Intent.EMOTIONAL_SUPPORT
