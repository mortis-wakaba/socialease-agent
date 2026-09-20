"""Intent routers for rule-based and optional LLM-backed workflow selection."""

import json
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm.base import (
    BaseLLMClient,
    JSONModeLLMClient,
    StructuredJSONLLMClient,
)
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
from app.observability.turn_latency import mark_turn_flag


class BaseIntentRouter(Protocol):
    """Interface for workflow intent routers."""

    async def route(self, message: str, safety_result: SafetyResult) -> IntentResult:
        """Route a user message to a workflow intent."""
        ...


class LLMSecondaryIntentPrediction(BaseModel):
    """One untrusted secondary intent emitted by the semantic router."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    intent: Intent = Field(alias="i")
    confidence: float = Field(alias="c", ge=0.0, le=1.0)
    relation: IntentRelation = Field(alias="r")
    explicitly_requested: bool = Field(alias="e")
    parameters_complete: bool = Field(alias="p")


class LLMIntentPrediction(BaseModel):
    """Strict provider response before application-owned plan compilation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    intent: Intent = Field(alias="i")
    confidence: float = Field(alias="c", ge=0.0, le=1.0)
    explicitly_requested: bool = Field(alias="e")
    parameters_complete: bool = Field(alias="p")
    secondary_intents: list[LLMSecondaryIntentPrediction] = Field(
        default_factory=list,
        alias="s",
        max_length=2,
    )


INTENT_ROUTER_SCHEMA_NAME = "socialease_intent_prediction"
INTENT_ROUTER_SINGLE_OUTPUT_TOKENS = 64
INTENT_ROUTER_MULTI_OUTPUT_TOKENS = 128
INTENT_ROUTER_PRIMARY_REASON = "Bounded semantic match."
INTENT_ROUTER_SECONDARY_REASON = "Bounded secondary action."

_SECONDARY_ACTION_CONNECTORS = (
    "然后",
    "接着",
    "同时",
    "并且",
    "还要",
    "再帮我",
    "and then",
    "also",
)
_SECONDARY_ACTION_BLOCKERS = (
    "不要",
    "不想",
    "停止",
    "暂停",
    "以后再",
    "之后再",
    "先放一边",
    "不是现在",
    "not now",
    "later instead",
)


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
            if isinstance(self.llm_client, StructuredJSONLLMClient) and (
                self.llm_client.json_mode_enabled
            ):
                json_schema, max_tokens = build_intent_router_json_schema(message)
                response = await self.llm_client.generate_structured_json_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    json_schema=json_schema,
                    schema_name=INTENT_ROUTER_SCHEMA_NAME,
                    temperature=0.0,
                    max_tokens=max_tokens,
                )
            elif (
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
            mark_turn_flag("routing_llm_used")
            return result.model_copy(update={"llm_usage": LLMUsage(used=True)})
        except json.JSONDecodeError:
            mark_turn_flag("routing_fallback")
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
            mark_turn_flag("routing_fallback")
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
            mark_turn_flag("routing_fallback")
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


def build_intent_router_json_schema(
    message: str,
) -> tuple[dict[str, object], int]:
    """Build a compact schema and allow queued actions only on explicit evidence."""
    intent_values = [intent.value for intent in Intent if intent is not Intent.CRISIS]
    relation_values = [relation.value for relation in IntentRelation]
    maximum_secondaries = 2 if _has_explicit_secondary_action(message) else 0
    secondary_schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "i": {"type": "string", "enum": intent_values},
            "c": {"type": "number", "minimum": 0, "maximum": 1},
            "r": {"type": "string", "enum": relation_values},
            "e": {"type": "boolean"},
            "p": {"type": "boolean"},
        },
        "required": ["i", "c", "r", "e", "p"],
    }
    properties: dict[str, object] = {
        "i": {"type": "string", "enum": intent_values},
        "c": {"type": "number", "minimum": 0, "maximum": 1},
        "e": {"type": "boolean"},
        "p": {"type": "boolean"},
    }
    required = ["i", "c", "e", "p"]
    if maximum_secondaries:
        properties["s"] = {
            "type": "array",
            "items": secondary_schema,
            "maxItems": maximum_secondaries,
        }
        required.append("s")
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }
    max_tokens = (
        INTENT_ROUTER_MULTI_OUTPUT_TOKENS
        if maximum_secondaries
        else INTENT_ROUTER_SINGLE_OUTPUT_TOKENS
    )
    return schema, max_tokens


def _has_explicit_secondary_action(message: str) -> bool:
    """Require a sequencing connector and two deterministic action signals."""
    normalized = message.casefold()
    if not any(term in normalized for term in _SECONDARY_ACTION_CONNECTORS):
        return False
    if any(term in normalized for term in _SECONDARY_ACTION_BLOCKERS):
        return False
    matched_intents = sum(
        any(term in normalized for term in terms)
        for terms in RuleBasedIntentRouter.intent_terms.values()
    )
    return matched_intents >= 2


def compile_llm_intent_prediction(
    payload: object,
    *,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> IntentResult:
    """Validate an LLM proposal and compile a server-owned sequential plan."""
    normalized = _normalize_intent_payload(payload)
    prediction = LLMIntentPrediction.model_validate(normalized)
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
        accepted.append(
            SecondaryIntent(
                intent=candidate.intent,
                confidence=candidate.confidence,
                relation=candidate.relation,
                reason=INTENT_ROUTER_SECONDARY_REASON,
            )
        )
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
        reason=INTENT_ROUTER_PRIMARY_REASON,
        secondary_intents=accepted,
        execution_plan=IntentExecutionPlan(
            current_intent=prediction.intent,
            pending_intents=[candidate.intent for candidate in accepted],
            steps=steps,
        ),
    )


def _normalize_intent_payload(payload: object) -> object:
    """Accept old verbose reason fields while emitting only the compact contract."""
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    normalized.pop("reason", None)
    secondaries = normalized.get("secondary_intents", normalized.get("s"))
    if isinstance(secondaries, list):
        normalized_secondaries: list[object] = []
        for item in secondaries:
            if isinstance(item, dict):
                item = dict(item)
                item.pop("reason", None)
            normalized_secondaries.append(item)
        if "s" in normalized:
            normalized["s"] = normalized_secondaries
        else:
            normalized["secondary_intents"] = normalized_secondaries
    return normalized


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


def reconcile_intent_risk_policy(
    result: IntentResult,
    *,
    risk_level: RiskLevel,
) -> IntentResult:
    """Tighten a speculative low-risk plan using authoritative input safety."""
    plan = result.execution_plan
    if plan is None or risk_level is RiskLevel.LOW:
        return result
    changed = False
    steps: list[IntentExecutionStep] = []
    for step in plan.steps:
        must_wait = risk_level in {RiskLevel.HIGH, RiskLevel.CRISIS} or (
            risk_level is RiskLevel.MEDIUM
            and step.intent is Intent.EXPOSURE_PLANNING
        )
        if must_wait and step.status is not IntentStepStatus.AWAITING_CONSENT:
            step = step.model_copy(
                update={
                    "status": IntentStepStatus.AWAITING_CONSENT,
                    "undo_supported": False,
                    "effect": None,
                }
            )
            changed = True
        steps.append(step)
    if not changed:
        return result
    return result.model_copy(
        update={"execution_plan": plan.model_copy(update={"steps": steps})}
    )


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
