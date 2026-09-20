"""Low-latency local triage for semantic output-boundary risks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


LOCAL_OUTPUT_RISK_CATEGORIES = frozenset(
    {
        "diagnosis",
        "treatment_promise",
        "dependency_encouragement",
        "real_support_discouragement",
        "coercive_practice",
        "unsafe_situation_reframing",
        "invented_user_fact",
    }
)


@dataclass(frozen=True)
class LocalOutputRiskPrediction:
    """One bounded local decision used only to escalate semantic review."""

    needs_semantic_review: bool
    candidate_categories: tuple[str, ...] = ()
    maximum_score: float = 0.0


class ShadowOutputRiskClassifier(Protocol):
    """Async local semantic classifier observed without owning enforcement."""

    async def predict(
        self,
        *,
        user_message: str,
        response: str,
    ) -> LocalOutputRiskPrediction:
        """Predict bounded output-risk candidates without making a decision."""


class LocalOutputRiskClassifier:
    """Score compositional Chinese risk features without an LLM round trip."""

    model_name = "weighted-output-risk-zh"
    model_version = "v1"
    review_threshold = 1.0

    def predict(
        self,
        *,
        user_message: str,
        response: str,
    ) -> LocalOutputRiskPrediction:
        """Return candidates for API review; never make the final allow decision."""
        normalized_user = _compact(user_message)
        normalized_response = _compact(response)
        scores = {
            "diagnosis": self._diagnosis_score(normalized_response),
            "treatment_promise": self._treatment_promise_score(
                normalized_response
            ),
            "dependency_encouragement": self._dependency_score(
                normalized_response
            ),
            "real_support_discouragement": self._support_discouragement_score(
                normalized_response
            ),
            "coercive_practice": self._coercion_score(
                user_message=normalized_user,
                response=normalized_response,
            ),
            "unsafe_situation_reframing": self._unsafe_reframing_score(
                user_message=normalized_user,
                response=normalized_response,
            ),
            "invented_user_fact": self._invented_fact_score(
                user_message=normalized_user,
                response=normalized_response,
            ),
        }
        candidates = tuple(
            category
            for category, score in scores.items()
            if score >= self.review_threshold
        )
        return LocalOutputRiskPrediction(
            needs_semantic_review=bool(candidates),
            candidate_categories=candidates,
            maximum_score=max(scores.values(), default=0.0),
        )

    @staticmethod
    def _diagnosis_score(response: str) -> float:
        certainty = _contains_any(
            response,
            ("就是", "说明你", "表明你", "足以证明", "可以断定", "属于"),
        )
        disease = _contains_any(
            response,
            ("心理疾病", "心理障碍", "焦虑症", "抑郁症", "社交恐惧症"),
        )
        return float(certainty) * 0.55 + float(disease) * 0.55

    @staticmethod
    def _treatment_promise_score(response: str) -> float:
        certain_outcome = _contains_any(
            response,
            ("彻底消失", "完全恢复", "肯定会好", "一定有效", "保证有效"),
        )
        intervention = _contains_any(
            response,
            ("照做", "练几次", "坚持", "这个练习", "这个方案", "步骤"),
        )
        return float(certain_outcome) * 0.65 + float(intervention) * 0.4

    @staticmethod
    def _dependency_score(response: str) -> float:
        exclusive = _contains_any(
            response,
            ("唯一", "只来", "只和", "只跟", "只需要", "就足够", "就够了"),
        )
        assistant_target = _contains_any(
            response,
            ("这里", "和我", "跟我", "本系统", "倾诉对象", "理解你的地方"),
        )
        return float(exclusive) * 0.55 + float(assistant_target) * 0.55

    @staticmethod
    def _support_discouragement_score(response: str) -> float:
        support_target = _contains_any(
            response,
            (
                "身边的人",
                "现实里的人",
                "家人",
                "朋友",
                "老师",
                "辅导员",
                "心理中心",
                "现实支持",
            ),
        )
        discouragement = _contains_any(
            response,
            (
                "不会理解",
                "无法真正理解",
                "只会更糟",
                "只会让问题变得更糟",
                "只会让事情更麻烦",
                "帮不了你",
                "没有必要告诉",
                "不要联系",
                "不必联系",
            ),
        )
        return float(support_target) * 0.5 + float(discouragement) * 0.6

    @staticmethod
    def _coercion_score(*, user_message: str, response: str) -> float:
        stop_or_discomfort = _contains_any(
            user_message + response,
            ("不舒服", "不适", "暂停", "退出", "停下来", "中途退出"),
        )
        pressure = _contains_any(
            response,
            (
                "所以要继续",
                "必须继续",
                "不能退出",
                "不能暂停",
                "不要把退出当成选项",
                "完成以后才能结束",
                "没有认真面对问题",
            ),
        )
        return float(stop_or_discomfort) * 0.45 + float(pressure) * 0.65

    @staticmethod
    def _unsafe_reframing_score(*, user_message: str, response: str) -> float:
        external_threat = _contains_any(
            user_message,
            ("威胁", "堵我", "打我", "跟踪", "霸凌", "强迫", "伤害我"),
        )
        dismissal = _contains_any(
            response,
            (
                "只是你的负面想法",
                "只是焦虑",
                "普通玩笑",
                "不是真实风险",
                "不需要当成真实风险",
                "没有必要按真实威胁处理",
            ),
        )
        return float(external_threat) * 0.5 + float(dismissal) * 0.6

    @staticmethod
    def _invented_fact_score(*, user_message: str, response: str) -> float:
        drafting_request = _contains_any(
            user_message,
            ("帮我写", "帮我组织", "怎么说", "写一句", "写一段"),
        )
        first_person = _contains_any(response, ("我", "我的", "我们家"))
        consequential_claims = (
            "被同学打",
            "被人打",
            "受伤",
            "失恋",
            "分手",
            "父母离婚",
            "结束了一段恋爱关系",
            "家人去世",
            "从高中开始",
            "从小就",
            "一直无法",
            "患有",
            "确诊",
            "住院",
            "做了心脏手术",
        )
        unsupported_claim = any(
            claim in response and claim not in user_message
            for claim in consequential_claims
        )
        unsupported_schedule_detail = _has_unsupported_schedule_detail(
            user_message=user_message,
            response=response,
        )
        personal_schedule_claim = bool(
            re.search(
                r"(?:我|我们).{0,12}(?:明天|后天|早课|晚课|考试|值班|开会|面试|"
                r"周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}点|"
                r"[零一二两三四五六七八九十]{1,3}点)",
                response,
            )
        )
        unsupported_personal_schedule = unsupported_schedule_detail and (
            drafting_request or personal_schedule_claim
        )
        return (
            float(drafting_request) * 0.35
            + float(first_person) * 0.2
            + float(unsupported_claim) * 0.8
            + float(unsupported_personal_schedule) * 0.8
        )


def _compact(text: str) -> str:
    return "".join(text.casefold().split())


def _contains_any(text: str, candidates: tuple[str, ...]) -> bool:
    return any(candidate.casefold() in text for candidate in candidates)


_SCHEDULE_DETAIL_PATTERN = re.compile(
    r"明天|后天|大后天|早课|晚课|考试|值班|开会|面试|"
    r"周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"(?:凌晨|早上|上午|中午|下午|晚上)?"
    r"(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})点"
    r"(?:钟|半|(?:\d{1,2}|[零一二三四五六七八九十]{1,2})分|"
    r"前|后|之前|之后|以后)"
)


def _has_unsupported_schedule_detail(*, user_message: str, response: str) -> bool:
    """Detect concrete schedule details introduced only by the generated reply."""
    return any(
        match.group(0) not in user_message
        for match in _SCHEDULE_DETAIL_PATTERN.finditer(response)
    )
