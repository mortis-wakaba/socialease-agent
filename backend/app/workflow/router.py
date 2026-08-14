"""Intent routers for rule-based and optional LLM-backed workflow selection."""

import json
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm.base import BaseLLMClient, JSONModeLLMClient
from app.llm.retry import ProviderError
from app.llm.prompts import (
    build_intent_router_system_prompt,
    build_intent_router_user_prompt,
)
from app.models import (
    Intent,
    IntentExecutionPlan,
    IntentExecutionStep,
    IntentAuthorizationBasis,
    IntentRelation,
    IntentResult,
    IntentStepStatus,
    RiskLevel,
    SafetyResult,
    SecondaryIntent,
)
from app.models_llm import LLMUsage


class BaseIntentRouter(Protocol):
    """Interface for workflow intent routers."""

    async def route(self, message: str, safety_result: SafetyResult) -> IntentResult:
        """Route a user message to a workflow intent."""
        ...


class LLMSecondaryIntentPrediction(BaseModel):
    """One untrusted secondary intent emitted by the semantic router."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    relation: IntentRelation
    reason: str = Field(min_length=1, max_length=240)
    explicitly_requested: bool
    parameters_complete: bool


class LLMIntentPrediction(BaseModel):
    """Strict provider response before application-owned plan compilation."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=240)
    explicitly_requested: bool
    parameters_complete: bool
    secondary_intents: list[LLMSecondaryIntentPrediction] = Field(max_length=2)


_EXECUTABLE_SECONDARY_INTENTS = {
    Intent.ROLEPLAY_PRACTICE,
    Intent.CBT_WORKSHEET,
    Intent.EXPOSURE_PLANNING,
    Intent.CAMPUS_RESOURCE_QUERY,
    Intent.PROGRESS_REVIEW,
    Intent.CALENDAR_PLANNING,
}


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
            system_prompt = build_intent_router_system_prompt()
            user_prompt = build_intent_router_user_prompt(message)
            if (
                isinstance(self.llm_client, JSONModeLLMClient)
                and self.llm_client.json_mode_enabled
            ):
                response = await self.llm_client.generate_json_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.0,
                )
            else:
                response = await self.llm_client.generate_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.0,
                )
            payload = json.loads(response)
            if not isinstance(payload, dict):
                raise ValueError("Intent router response must be an object.")
            result = compile_llm_intent_prediction(
                payload,
                risk_level=safety_result.risk_level,
            )
            result = apply_llm_intent_policy(result)
            return result.model_copy(update={"llm_usage": LLMUsage(used=True)})
        except json.JSONDecodeError:
            fallback = await self.fallback_router.route(message, safety_result)
            return fallback.model_copy(
                update={
                    "llm_usage": LLMUsage(
                        fallback_used=True,
                        error_category="INVALID_JSON",
                    )
                }
            )
        except (ValueError, ValidationError):
            fallback = await self.fallback_router.route(message, safety_result)
            return fallback.model_copy(
                update={
                    "llm_usage": LLMUsage(
                        fallback_used=True,
                        error_category="INVALID_SCHEMA",
                    )
                }
            )
        except Exception as exc:
            fallback = await self.fallback_router.route(message, safety_result)
            category = (
                exc.category.value
                if isinstance(exc, ProviderError)
                else "TRANSIENT_PROVIDER_ERROR"
            )
            return fallback.model_copy(
                update={
                    "llm_usage": LLMUsage(
                        fallback_used=True,
                        error_category=category,
                    )
                }
            )


def compile_llm_intent_prediction(
    payload: object,
    *,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> IntentResult:
    """Validate an LLM proposal and compile a server-owned sequential plan."""
    prediction = LLMIntentPrediction.model_validate(payload)
    if prediction.intent == Intent.CRISIS:
        raise ValueError("LLM router cannot emit crisis outside safety routing.")

    accepted: list[SecondaryIntent] = []
    steps = [
        _execution_step(
            intent=prediction.intent,
            relation=None,
            explicitly_requested=prediction.explicitly_requested,
            parameters_complete=prediction.parameters_complete,
            risk_level=risk_level,
            position=0,
        )
    ]
    seen = {prediction.intent}
    for candidate in prediction.secondary_intents:
        if candidate.intent == Intent.CRISIS:
            raise ValueError("LLM router cannot queue crisis as a secondary intent.")
        if (
            candidate.confidence < 0.6
            or candidate.intent not in _EXECUTABLE_SECONDARY_INTENTS
            or candidate.intent in seen
        ):
            continue
        if candidate.relation == IntentRelation.CONFLICTING:
            return _clarification_for_conflicting_intents(prediction, candidate)
        seen.add(candidate.intent)
        accepted.append(SecondaryIntent.model_validate(candidate.model_dump()))
        steps.append(
            _execution_step(
                intent=candidate.intent,
                relation=candidate.relation,
                explicitly_requested=candidate.explicitly_requested,
                parameters_complete=candidate.parameters_complete,
                risk_level=risk_level,
                position=len(steps),
            )
        )

    return IntentResult(
        intent=prediction.intent,
        confidence=prediction.confidence,
        reason=prediction.reason,
        secondary_intents=accepted,
        execution_plan=IntentExecutionPlan(
            current_intent=prediction.intent,
            pending_intents=[candidate.intent for candidate in accepted],
            steps=steps,
        ),
    )


def _execution_step(
    *,
    intent: Intent,
    relation: IntentRelation | None,
    explicitly_requested: bool,
    parameters_complete: bool,
    risk_level: RiskLevel,
    position: int,
) -> IntentExecutionStep:
    """Convert semantic evidence into an application-owned authorization state."""
    basis = (
        IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
        if explicitly_requested
        else IntentAuthorizationBasis.INFERRED
    )
    if (
        not explicitly_requested
        or not parameters_complete
        or risk_level in {RiskLevel.HIGH, RiskLevel.CRISIS}
        or (intent == Intent.EXPOSURE_PLANNING and risk_level == RiskLevel.MEDIUM)
    ):
        status = IntentStepStatus.AWAITING_CONSENT
        undo_supported = False
    elif intent == Intent.CALENDAR_PLANNING:
        status = IntentStepStatus.READY_WITH_UNDO
        undo_supported = True
    else:
        status = IntentStepStatus.READY
        undo_supported = False
    step_key = f"{position}:{intent.value}:{relation.value if relation else 'primary'}"
    return IntentExecutionStep(
        step_id=sha256(step_key.encode("utf-8")).hexdigest()[:24],
        intent=intent,
        relation=relation,
        authorization_basis=basis,
        status=status,
        undo_supported=undo_supported,
    )


def _clarification_for_conflicting_intents(
    prediction: LLMIntentPrediction,
    conflicting: LLMSecondaryIntentPrediction,
) -> IntentResult:
    """Turn an unresolved multi-intent conflict into one safe clarification step."""
    return IntentResult(
        intent=Intent.CLARIFICATION_NEEDED,
        confidence=max(0.6, min(prediction.confidence, conflicting.confidence)),
        reason=(
            "The request contains conflicting actions without a safe execution "
            "order; clarification is required."
        ),
        execution_plan=IntentExecutionPlan(
            current_intent=Intent.CLARIFICATION_NEEDED,
            steps=[
                _execution_step(
                    intent=Intent.CLARIFICATION_NEEDED,
                    relation=None,
                    explicitly_requested=True,
                    parameters_complete=True,
                    risk_level=RiskLevel.LOW,
                    position=0,
                )
            ],
        ),
    )


def apply_llm_intent_policy(result: IntentResult) -> IntentResult:
    """Apply the production confidence floor to a validated LLM prediction."""
    if (
        result.confidence < 0.6
        and result.intent not in {Intent.EMOTIONAL_SUPPORT, Intent.OUT_OF_SCOPE}
    ):
        return IntentResult(
            intent=Intent.CLARIFICATION_NEEDED,
            confidence=max(0.6, result.confidence),
            reason=(
                "The semantic router was not confident enough to start a "
                "specialized action; clarification is required."
            ),
            execution_plan=IntentExecutionPlan(
                current_intent=Intent.CLARIFICATION_NEEDED,
                steps=[
                    _execution_step(
                        intent=Intent.CLARIFICATION_NEEDED,
                        relation=None,
                        explicitly_requested=True,
                        parameters_complete=True,
                        risk_level=RiskLevel.LOW,
                        position=0,
                    )
                ],
            ),
        )
    return result


class RuleBasedIntentRouter:
    """Route safe messages with transparent keyword scoring rules."""

    stop_practice_terms: tuple[str, ...] = (
        "不想继续",
        "先暂停",
        "暂停练习",
        "停止练习",
        "不要继续",
        "不想练了",
        "不想角色扮演",
        "退出练习",
        "停止 roleplay",
        "停止 role play",
        "stop roleplay",
        "stop role play",
        "pause roleplay",
        "pause role play",
        "stop practice",
        "pause practice",
    )

    clarification_terms: tuple[str, ...] = (
        "帮帮我",
        "帮一下我",
        "不知道怎么办",
        "还没想好",
        "说不清楚",
        "不知道从哪说",
        "can you help me",
        "not sure what to do",
    )

    out_of_scope_terms: tuple[str, ...] = (
        "高数题",
        "数学题",
        "微积分",
        "线性代数",
        "物理题",
        "化学题",
        "写代码",
        "改代码",
        "代码报错",
        "python 程序",
        "java 程序",
        "c++",
        "网络爬虫",
        "股票推荐",
        "哪只股票",
        "预测股价",
        "天气预报",
        "明天天气",
        "做菜食谱",
        "旅游攻略",
        "latest news",
        "weather forecast",
        "stock price",
    )

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
        Intent.CALENDAR_PLANNING: (
            "日历",
            "提醒我练习",
            "设置提醒",
            "添加提醒",
            "每天提醒",
            "每周提醒",
            "calendar reminder",
            "schedule practice",
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
        if self._is_stop_practice_request(normalized):
            return IntentResult(
                intent=Intent.EMOTIONAL_SUPPORT,
                confidence=0.84,
                reason="User asked to pause or stop active practice; routed to support.",
            )

        if self._is_out_of_scope_request(normalized):
            return IntentResult(
                intent=Intent.OUT_OF_SCOPE,
                confidence=0.9,
                reason="Request matched an explicit out-of-scope subject boundary.",
            )

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

        if self._needs_clarification(normalized):
            return IntentResult(
                intent=Intent.CLARIFICATION_NEEDED,
                confidence=0.72,
                reason=(
                    "The request appears in-domain but lacks enough information to "
                    "select a useful response or specialized action."
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

    def _is_stop_practice_request(self, message: str) -> bool:
        """Return whether the user is asking to pause or stop practice."""
        return any(term in message for term in self.stop_practice_terms)

    def _needs_clarification(self, message: str) -> bool:
        """Recognize short, underspecified help requests without guessing an action."""
        normalized = message.strip("。！？!?，, ")
        if normalized in {"帮我", "怎么办", "help", "help me"}:
            return True
        return len(normalized) <= 32 and any(
            term in normalized for term in self.clarification_terms
        )

    def _is_out_of_scope_request(self, message: str) -> bool:
        """Recognize only explicit non-product subjects in deterministic fallback."""
        return any(term in message for term in self.out_of_scope_terms)


IntentRouter = RuleBasedIntentRouter
