"""Tests for the MVP safety classifier."""

import pytest

from app.models import RiskLevel
from app.safety.classifier import SafetyClassifier


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我明天要小组讨论，有点紧张", RiskLevel.LOW),
        ("我想练习向老师提问，但不知道怎么开口", RiskLevel.LOW),
        ("我焦虑到睡不着，感觉有点崩溃", RiskLevel.MEDIUM),
        ("想到社团破冰我很害怕", RiskLevel.MEDIUM),
        ("I feel overwhelmed before the interview", RiskLevel.MEDIUM),
        ("我被威胁了，不知道该怎么办", RiskLevel.HIGH),
        ("室友一直霸凌我，我很害怕", RiskLevel.HIGH),
        ("I had a panic attack and can't breathe", RiskLevel.HIGH),
        ("我不想活了，想伤害自己", RiskLevel.CRISIS),
        ("我想自杀", RiskLevel.CRISIS),
        ("I want to kill myself tonight", RiskLevel.CRISIS),
        ("我怕自己会伤害他人", RiskLevel.CRISIS),
    ],
)
def test_classifier_rule_based_cases(message: str, expected: RiskLevel) -> None:
    classifier = SafetyClassifier()

    result = classifier.classify(message)

    assert result.risk_level == expected
    assert result.reason


def test_classifier_prioritizes_crisis_over_medium_terms() -> None:
    classifier = SafetyClassifier()

    result = classifier.classify("我崩溃了，也不想活了")

    assert result.risk_level == RiskLevel.CRISIS
