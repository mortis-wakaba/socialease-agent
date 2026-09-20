"""Safety classifiers with deterministic floor plus optional LLM enhancement."""

import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm.base import BaseLLMClient, JSONModeLLMClient
from app.llm.factory import create_llm_client
from app.llm.retry import ProviderError
from app.llm.prompts import build_safety_system_prompt, build_safety_user_prompt
from app.models import RiskLevel, SafetyResult
from app.models_llm import LLMUsage


class BaseSafetyClassifier(Protocol):
    """Interface for async safety classifiers."""

    async def classify(self, message: str) -> SafetyResult:
        """Classify a user message into a conservative risk level."""
        ...


class RuleBasedSafetyClassifier:
    """Provide a deterministic crisis floor before semantic classification."""

    crisis_terms: tuple[str, ...] = (
        "自杀",
        "轻生",
        "不想活",
        "不想再活",
        "活着没意思",
        "撑不下去",
        "撑不住了",
        "消失算了",
        "永远睡过去",
        "不想醒来",
        "结束生命",
        "结束这一切",
        "离开这个世界",
        "伤害自己",
        "自残",
        "弄死自己",
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
    high_terms: tuple[str, ...] = ()
    medium_terms: tuple[str, ...] = ()

    async def classify(self, message: str) -> SafetyResult:
        """Return the highest matched risk level and a short reason."""
        normalized = message.casefold()

        crisis_hit = self._first_unnegated_match(normalized, self.crisis_terms)
        if crisis_hit is not None:
            return SafetyResult(
                risk_level=RiskLevel.CRISIS,
                reason=f"Matched crisis safety term: {crisis_hit}",
            )

        return SafetyResult(
            risk_level=RiskLevel.LOW,
            reason="No deterministic crisis expression detected; defer to semantic safety classification.",
        )

    @classmethod
    def _first_unnegated_match(
        cls,
        message: str,
        terms: tuple[str, ...],
    ) -> str | None:
        """Return the first crisis term not explicitly denied by nearby text."""
        for term in terms:
            start = 0
            while True:
                index = message.find(term, start)
                if index < 0:
                    break
                prefix = message[max(0, index - 20) : index]
                if not cls._explicitly_negated(prefix):
                    return term
                start = index + len(term)
        return None

    @staticmethod
    def _explicitly_negated(prefix: str) -> bool:
        return prefix.endswith(
            (
                "不是想",
                "并不是想",
                "并非想",
                "并非要",
                "不想",
                "不会",
                "不打算",
                "不准备",
                "没有想过",
                "没想过",
                "从没想过",
                "don't want to ",
                "do not want to ",
                "not going to ",
                "never considered ",
            )
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
        kwargs = {
            "system_prompt": build_safety_system_prompt(),
            "user_prompt": build_safety_user_prompt(message),
            "temperature": 0.0,
        }
        response = (
            await self.llm_client.generate_json_text(**kwargs)
            if isinstance(self.llm_client, JSONModeLLMClient)
            and self.llm_client.json_mode_enabled
            else await self.llm_client.generate_text(**kwargs)
        )
        payload = json.loads(response)
        if not isinstance(payload, dict):
            raise ValueError("Safety classifier response must be an object.")
        normalized = dict(payload)
        normalized.pop("reason", None)  # Accept the previous verbose contract.
        prediction = _CompactSafetyPrediction.model_validate(normalized)
        return SafetyResult(
            risk_level=prediction.risk_level,
            reason=f"Semantic classifier selected {prediction.risk_level.value} risk.",
            llm_usage=LLMUsage(used=True),
        )


class _CompactSafetyPrediction(BaseModel):
    """Minimal provider contract; audit wording remains application-owned."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    risk_level: RiskLevel = Field(alias="r")


class HybridSafetyClassifier:
    """Use a crisis rule floor and delegate every other level to the LLM."""

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
            return _semantic_safety_failure_result("LLM_DISABLED")
        try:
            llm_result = await self.llm_classifier.classify(message)
        except (ValueError, json.JSONDecodeError, ValidationError):
            return _semantic_safety_failure_result("INVALID_JSON")
        except Exception as exc:
            category = (
                exc.category.value
                if isinstance(exc, ProviderError)
                else "TRANSIENT_PROVIDER_ERROR"
            )
            return _semantic_safety_failure_result(category)
        return llm_result


def _semantic_safety_failure_result(error_category: str) -> SafetyResult:
    """Preserve opt-out fallback but fail closed on configured-provider errors."""
    if error_category == "LLM_DISABLED":
        return SafetyResult(
            risk_level=RiskLevel.LOW,
            reason=(
                "Semantic safety classification is intentionally disabled; "
                "using the deterministic crisis floor."
            ),
            llm_usage=LLMUsage(
                fallback_used=True,
                error_category=error_category,
            ),
        )
    return SafetyResult(
        risk_level=RiskLevel.HIGH,
        reason=(
            "Semantic safety classification was unavailable; using a conservative "
            "high-risk fallback instead of low-risk streaming."
        ),
        llm_usage=LLMUsage(
            fallback_used=True,
            error_category=error_category,
        ),
    )


def create_safety_classifier() -> BaseSafetyClassifier:
    """Build the default hybrid classifier for runtime use."""
    llm_client = create_llm_client()
    llm_classifier = LlmSafetyClassifier(llm_client) if llm_client is not None else None
    return HybridSafetyClassifier(llm_classifier=llm_classifier)


SafetyClassifier = RuleBasedSafetyClassifier
