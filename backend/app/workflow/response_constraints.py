"""Extract safe presentation constraints independently from business intent."""

import re

from app.models_support_generation import PresentationConstraints


def extract_response_constraints(message: str) -> PresentationConstraints:
    """Return only explicit, machine-checkable response presentation preferences."""
    max_chars = None
    length_match = re.search(
        r"(?:不超过|最多|控制在)\s*(\d{1,4})\s*(?:个)?字",
        message,
    )
    if length_match:
        max_chars = min(1000, max(10, int(length_match.group(1))))

    item_count = None
    item_match = re.search(
        r"(?:给我|列出|提供|写)?\s*([1-5一二三四五])\s*(?:条|点|个)(?:建议|步骤|方法|要点)?",
        message,
    )
    if item_match:
        item_count = _number_value(item_match.group(1))

    output_format = "plain"
    if re.search(r"(?:只(?:回复|回答|输出|给)?|帮我写)?\s*(?:一|1)句话", message):
        output_format = "single_sentence"
    elif item_count is not None or "分点" in message or "列点" in message:
        output_format = "steps"
    if "不要分点" in message or "不要列点" in message:
        output_format = "plain"

    requested_language = None
    if re.search(r"(?:用|请用)(?:英文|英语)|in english", message, re.IGNORECASE):
        requested_language = "en"
    elif re.search(r"(?:用|请用)(?:中文|汉语)|in chinese", message, re.IGNORECASE):
        requested_language = "zh"

    brief = (
        max_chars is not None
        or output_format == "single_sentence"
        or any(term in message for term in ("简短", "简洁", "精简"))
    )
    plain_language = any(
        term in message
        for term in ("不要专业名词", "不用专业名词", "通俗一点", "简单易懂")
    )
    return PresentationConstraints(
        verbosity="brief" if brief else "normal",
        max_chars=max_chars,
        output_format=output_format,
        requested_language=requested_language,
        item_count=item_count,
        plain_language=plain_language,
    )


def _number_value(value: str) -> int:
    words = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    return words[value] if value in words else int(value)
