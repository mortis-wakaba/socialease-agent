"""Intent routers for rule-based and optional LLM-backed workflow selection."""

import json
from typing import Protocol

from pydantic import ValidationError

from app.llm.base import BaseLLMClient
from app.llm.prompts import (
    build_intent_router_system_prompt,
    build_intent_router_user_prompt,
)
from app.models import Intent, IntentResult, RiskLevel, SafetyResult
from app.models_llm import LLMUsage


class BaseIntentRouter(Protocol):
    """Interface for workflow intent routers."""

    async def route(self, message: str, safety_result: SafetyResult) -> IntentResult:
        """Route a user message to a workflow intent."""
        ...


class LlmIntentRouter:
    """Prefer LLM semantic routing while preserving rule-based fallback."""

    def __init__(
        self,
        *,
        llm_client: BaseLLMClient,
        fallback_router: "RuleBasedIntentRouter | None" = None,
    ) -> None:
        self.llm_client = llm_client
        self.fallback_router = fallback_router or RuleBasedIntentRouter()

    async def route(self, message: str, safety_result: SafetyResult) -> IntentResult:
        """Route with LLM by default unless crisis or fallback is required."""
        if safety_result.risk_level == RiskLevel.CRISIS:
            return await self.fallback_router.route(message, safety_result)
        try:
            response = await self.llm_client.generate_text(
                system_prompt=build_intent_router_system_prompt(),
                user_prompt=build_intent_router_user_prompt(message),
                temperature=0.0,
            )
            payload = json.loads(response)
            if not isinstance(payload, dict):
                raise ValueError("Intent router response must be an object.")
            result = IntentResult.model_validate(payload)
            if result.intent == Intent.CRISIS:
                raise ValueError("LLM router cannot emit crisis outside safety routing.")
            return result.model_copy(update={"llm_usage": LLMUsage(used=True)})
        except (ValueError, json.JSONDecodeError, ValidationError):
            fallback = await self.fallback_router.route(message, safety_result)
        except Exception:
            fallback = await self.fallback_router.route(message, safety_result)
        return fallback.model_copy(update={"llm_usage": LLMUsage(fallback_used=True)})


class RuleBasedIntentRouter:
    """Route safe messages with transparent keyword scoring rules."""

    intent_terms: dict[Intent, tuple[str, ...]] = {
        Intent.ROLEPLAY_PRACTICE: (
            "角色扮演",
            "模拟",
            "练习对话",
            "扮演",
            "课堂发言",
            "小组讨论",
            "宿舍沟通",
            "社团破冰",
            "约同学吃饭",
            "向老师提问",
            "面试自我介绍",
            "拒绝别人",
            "表达不同意见",
            "roleplay",
            "role play",
        ),
        Intent.CBT_WORKSHEET: (
            "cbt",
            "自动想法",
            "想法记录",
            "认知",
            "证据支持",
            "证据反对",
            "替代想法",
            "worksheet",
            "thought record",
        ),
        Intent.EXPOSURE_PLANNING: (
            "暴露",
            "分级",
            "阶梯",
            "焦虑等级",
            "由易到难",
            "练习计划",
            "exposure",
            "ladder",
        ),
        Intent.CAMPUS_RESOURCE_QUERY: (
            "心理中心",
            "学校资源",
            "校内资源",
            "辅导员",
            "预约咨询",
            "在哪里求助",
            "campus resource",
        ),
        Intent.PROGRESS_REVIEW: (
            "进度",
            "复盘",
            "完成了",
            "练习记录",
            "最近表现",
            "progress",
            "review",
        ),
    }

    async def route(self, message: str, safety_result: SafetyResult) -> IntentResult:
        """Return a workflow intent, preserving crisis safety routing."""
        if safety_result.risk_level == RiskLevel.CRISIS:
            return IntentResult(
                intent=Intent.CRISIS,
                confidence=1.0,
                reason="Safety classifier required crisis escalation.",
            )

        normalized = message.casefold()
        scored_matches = self._score_intents(normalized)
        if scored_matches:
            best_intent, best_terms = scored_matches[0]
            score = len(best_terms)
            confidence = min(0.95, 0.58 + (score * 0.1))
            matched = ", ".join(best_terms[:4])
            return IntentResult(
                intent=best_intent,
                confidence=confidence,
                reason=(
                    f"Keyword scoring selected {best_intent.value} with "
                    f"{score} matched term(s): {matched}"
                ),
            )

        return IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=0.62,
            reason="No specific practice intent detected; defaulted to support.",
        )

    def _score_intents(self, message: str) -> list[tuple[Intent, list[str]]]:
        matches: list[tuple[Intent, list[str]]] = []
        for intent, terms in self.intent_terms.items():
            matched_terms = [term for term in terms if term in message]
            if matched_terms:
                matches.append((intent, matched_terms))

        return sorted(matches, key=lambda item: len(item[1]), reverse=True)


IntentRouter = RuleBasedIntentRouter
