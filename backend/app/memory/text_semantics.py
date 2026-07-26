"""Shared deterministic text semantics for memory retrieval and diagnostics."""

import re


_STOP_TERMS = {
    "一个",
    "一下",
    "可以",
    "怎么",
    "怎样",
    "什么",
    "现在",
    "这次",
    "还是",
    "觉得",
    "练习",
    "帮助",
    "用户",
}
_SCENARIO_TERMS: dict[str, tuple[str, ...]] = {
    "classroom_speech": ("课堂", "发言", "开场", "开口", "观点"),
    "group_discussion": ("小组", "讨论", "组会", "观点", "意见"),
    "dorm_conflict": ("宿舍", "室友", "边界", "请求", "沟通"),
    "club_icebreaking": ("社团", "破冰", "开场", "寒暄"),
    "invite_classmate_meal": ("邀请", "同学", "吃饭", "时间", "地点"),
    "ask_teacher_question": ("老师", "提问", "问题", "尝试"),
    "interview_self_intro": ("面试", "自我介绍", "经历"),
    "refuse_request": ("拒绝", "边界", "请求", "理由"),
    "express_disagreement": ("不同意见", "不同看法", "观点", "理由"),
}
_NEGATION_MARKERS = (
    "不再",
    "不要",
    "不想",
    "不适合",
    "没用",
    "没有帮助",
    "不是",
    "别再",
    "stop",
    "no longer",
    "do not",
    "don't",
)


def lexical_terms(text: str, scenario_type: str | None = None) -> set[str]:
    """Tokenize mixed Chinese/ASCII text with bounded domain expansion."""
    normalized = text.casefold()
    ascii_terms = re.findall(r"[a-z0-9]+", normalized)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    terms: list[str] = [*ascii_terms]
    for run in cjk_runs:
        terms.append(run)
        terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    if scenario_type is not None:
        terms.extend(_SCENARIO_TERMS.get(scenario_type, ()))
    return {
        term
        for term in terms
        if 2 <= len(term) <= 48 and term not in _STOP_TERMS
    }


def sql_query_terms(query: str, scenario_type: str | None) -> tuple[str, ...]:
    """Return selective bounded terms suitable for parameterized SQL LIKE."""
    del scenario_type  # Scenario is an independent hard filter.
    ranked = sorted(
        lexical_terms(query),
        key=lambda term: (len(term), term),
        reverse=True,
    )
    return tuple(ranked[:16])


def is_negative(text: str) -> bool:
    """Return whether text carries one of the bounded negation markers."""
    normalized = text.casefold()
    return any(marker in normalized for marker in _NEGATION_MARKERS)


def conflict_overlap(left: str, right: str) -> int:
    """Return lexical overlap only when two texts have opposing polarity."""
    if is_negative(left) == is_negative(right):
        return 0
    return len(lexical_terms(left).intersection(lexical_terms(right)))


def memories_conflict(
    left: str,
    right: str,
    *,
    minimum_overlap: int = 2,
) -> bool:
    """Apply the shared deterministic conflict rule."""
    return conflict_overlap(left, right) >= max(minimum_overlap, 1)
