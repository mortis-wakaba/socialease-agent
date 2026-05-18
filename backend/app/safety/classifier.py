"""Safety classifiers with deterministic floor plus optional LLM enhancement."""

import json
from typing import Protocol

from pydantic import ValidationError

from app.llm.base import BaseLLMClient
from app.llm.factory import create_llm_client
from app.llm.prompts import build_safety_system_prompt, build_safety_user_prompt
from app.models import RiskLevel, SafetyResult
from app.models_llm import LLMUsage


class BaseSafetyClassifier(Protocol):
    """Interface for async safety classifiers."""

    async def classify(self, message: str) -> SafetyResult:
        """Classify a user message into a conservative risk level."""
        ...


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

    async def classify(self, message: str) -> SafetyResult:
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


class LlmSafetyClassifier:
    """Semantic safety classifier using a configured LLM client."""

    def __init__(self, llm_client: BaseLLMClient) -> None:
        self.llm_client = llm_client

    async def classify(self, message: str) -> SafetyResult:
        """Return one validated semantic safety classification."""
        response = await self.llm_client.generate_text(
            system_prompt=build_safety_system_prompt(),
            user_prompt=build_safety_user_prompt(message),
            temperature=0.0,
        )
        payload = json.loads(response)
        if not isinstance(payload, dict):
            raise ValueError("Safety classifier response must be an object.")
        return SafetyResult.model_validate(payload).model_copy(
            update={"llm_usage": LLMUsage(used=True)}
        )


class HybridSafetyClassifier:
    """Use deterministic rules as a floor and let LLM raise risk when needed."""

    risk_rank: dict[RiskLevel, int] = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRISIS: 3,
    }

    def __init__(
        self,
        *,
        rule_classifier: RuleBasedSafetyClassifier | None = None,
        llm_classifier: LlmSafetyClassifier | None = None,
    ) -> None:
        self.rule_classifier = rule_classifier or RuleBasedSafetyClassifier()
        self.llm_classifier = llm_classifier

    async def classify(self, message: str) -> SafetyResult:
        """Return the higher-risk result, never downgrading deterministic safety."""
        rule_result = await self.rule_classifier.classify(message)
        if rule_result.risk_level == RiskLevel.CRISIS:
            return rule_result
        if self.llm_classifier is None:
            return rule_result
        try:
            llm_result = await self.llm_classifier.classify(message)
        except (ValueError, json.JSONDecodeError, ValidationError):
            return rule_result.model_copy(
                update={"llm_usage": LLMUsage(fallback_used=True)}
            )
        except Exception:
            return rule_result.model_copy(
                update={"llm_usage": LLMUsage(fallback_used=True)}
            )
        if self.risk_rank[llm_result.risk_level] > self.risk_rank[rule_result.risk_level]:
            return llm_result
        return rule_result.model_copy(update={"llm_usage": llm_result.llm_usage})


def create_safety_classifier() -> BaseSafetyClassifier:
    """Build the default hybrid classifier for runtime use."""
    llm_client = create_llm_client()
    llm_classifier = LlmSafetyClassifier(llm_client) if llm_client is not None else None
    return HybridSafetyClassifier(llm_classifier=llm_classifier)


SafetyClassifier = RuleBasedSafetyClassifier
