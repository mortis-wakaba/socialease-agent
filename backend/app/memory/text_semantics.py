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
    del scenario_type
    normalized = text.casefold()
    ascii_terms = re.findall(r"[a-z0-9]+", normalized)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    terms: list[str] = [*ascii_terms]
    for run in cjk_runs:
        terms.append(run)
        terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return {
        term
        for term in terms
        if 2 <= len(term) <= 48 and term not in _STOP_TERMS
    }


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
