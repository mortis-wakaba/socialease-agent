"""Tests for the MVP safety classifier."""

import pytest

from app.models import RiskLevel
from app.safety.classifier import RuleBasedSafetyClassifier


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我明天要小组讨论，有点紧张", RiskLevel.LOW),
        ("我想练习向老师提问，但不知道怎么开口", RiskLevel.LOW),
        ("我焦虑到睡不着，感觉有点崩溃", RiskLevel.LOW),
        ("想到社团破冰我很害怕", RiskLevel.LOW),
        ("I feel overwhelmed before the interview", RiskLevel.LOW),
        ("我被威胁了，不知道该怎么办", RiskLevel.LOW),
        ("室友一直霸凌我，我很害怕", RiskLevel.LOW),
        ("I had a panic attack and can't breathe", RiskLevel.LOW),
        ("我不想活了，想伤害自己", RiskLevel.CRISIS),
        ("我想自杀", RiskLevel.CRISIS),
        ("I want to kill myself tonight", RiskLevel.CRISIS),
        ("我怕自己会伤害他人", RiskLevel.CRISIS),
    ],
)
async def test_classifier_rule_based_cases(message: str, expected: RiskLevel) -> None:
    classifier = RuleBasedSafetyClassifier()

    result = await classifier.classify(message)

    assert result.risk_level == expected
    assert result.reason


@pytest.mark.anyio
async def test_classifier_prioritizes_crisis_over_medium_terms() -> None:
    classifier = RuleBasedSafetyClassifier()

    result = await classifier.classify("我崩溃了，也不想活了")

    assert result.risk_level == RiskLevel.CRISIS


@pytest.mark.anyio
async def test_classifier_does_not_trigger_on_explicitly_negated_self_harm() -> None:
    classifier = RuleBasedSafetyClassifier()

    result = await classifier.classify(
        "我不是想伤害自己，只是想练习怎么开口求助"
    )

    assert result.risk_level == RiskLevel.LOW


@pytest.mark.anyio
async def test_classifier_keeps_unnegated_crisis_signal_after_negation() -> None:
    classifier = RuleBasedSafetyClassifier()

    result = await classifier.classify("我不想自杀，但我现在真的想死")

    assert result.risk_level == RiskLevel.CRISIS
