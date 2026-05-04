"""Safety classifier interfaces and the rule-based MVP implementation."""

from typing import Protocol

from app.models import RiskLevel, SafetyResult


class BaseSafetyClassifier(Protocol):
    """Interface for safety classifiers, including future LLM classifiers."""

    def classify(self, message: str) -> SafetyResult:
        """Classify a user message into a conservative risk level."""
        ...


class LlmSafetyClassifier:
    """Placeholder adapter for a future LLM-based safety classifier."""

    def classify(self, message: str) -> SafetyResult:
        """Reserve the same interface for future model-backed classification."""
        raise NotImplementedError("LLM safety classifier is not implemented yet.")


class RuleBasedSafetyClassifier:
    """Classify user messages into conservative safety risk levels."""

    crisis_terms: tuple[str, ...] = (
        "自杀",
        "轻生",
        "不想活",
        "不想再活",
        "结束生命",
        "伤害自己",
        "割腕",
        "跳楼",
        "服药自杀",
        "想死",
        "杀了别人",
        "杀人",
        "伤害别人",
        "伤害他人",
        "kill myself",
        "suicide",
        "end my life",
        "hurt myself",
        "harm myself",
        "hurt someone",
        "kill someone",
    )
    high_terms: tuple[str, ...] = (
        "被威胁",
        "威胁我",
        "霸凌",
        "跟踪我",
        "恐吓",
        "想打人",
        "控制不住自己",
        "panic attack",
        "can't breathe",
        "cannot breathe",
    )
    medium_terms: tuple[str, ...] = (
        "崩溃",
        "焦虑到",
        "喘不过气",
        "很害怕",
        "害怕出门",
        "睡不着",
        "panic",
        "overwhelmed",
    )

    def classify(self, message: str) -> SafetyResult:
        """Return the highest matched risk level and a short reason."""
        normalized = message.casefold()

        crisis_hit = self._first_match(normalized, self.crisis_terms)
        if crisis_hit is not None:
            return SafetyResult(
                risk_level=RiskLevel.CRISIS,
                reason=f"Matched crisis safety term: {crisis_hit}",
            )

        high_hit = self._first_match(normalized, self.high_terms)
        if high_hit is not None:
            return SafetyResult(
                risk_level=RiskLevel.HIGH,
                reason=f"Matched high-risk safety term: {high_hit}",
            )

        medium_hit = self._first_match(normalized, self.medium_terms)
        if medium_hit is not None:
            return SafetyResult(
                risk_level=RiskLevel.MEDIUM,
                reason=f"Matched medium-risk stress term: {medium_hit}",
            )

        return SafetyResult(
            risk_level=RiskLevel.LOW,
            reason="No high-risk or crisis terms detected by MVP rules.",
        )

    @staticmethod
    def _first_match(message: str, terms: tuple[str, ...]) -> str | None:
        for term in terms:
            if term in message:
                return term
        return None


SafetyClassifier = RuleBasedSafetyClassifier
