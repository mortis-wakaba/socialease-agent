"""Small deterministic redactor for sensitive identifiers."""

import re


REDACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("national_id", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
    ),
    (
        "wechat",
        re.compile(r"(?:微信|微信号|wechat|weixin|wx)[:：\s]*[A-Za-z][A-Za-z0-9_-]{5,19}", re.I),
    ),
    (
        "qq",
        re.compile(r"(?:QQ|qq|QQ号|qq号)[:：\s]*[1-9][0-9]{4,11}", re.I),
    ),
    (
        "student_id",
        re.compile(r"(?:学号|student\s*id|student_id)[:：\s]*[A-Za-z0-9_-]{5,20}", re.I),
    ),
    (
        "address",
        re.compile(
            r"(?:住址|地址|宿舍|寝室|家庭住址)[:：\s]*[\u4e00-\u9fffA-Za-z0-9#栋单元室号楼路街巷弄-]{4,40}|"
            r"[\u4e00-\u9fff]{2,12}市[\u4e00-\u9fff]{1,12}[区县]"
            r"[\u4e00-\u9fffA-Za-z0-9#栋单元室号楼路街巷弄-]{2,40}"
        ),
    ),
    (
        "class_group",
        re.compile(
            r"(?:班级|专业班级)[:：\s]*[\u4e00-\u9fffA-Za-z0-9_-]{2,20}|"
            r"[\u4e00-\u9fffA-Za-z]{1,8}\d{2,4}班"
        ),
    ),
    (
        "organization",
        re.compile(
            r"(?:学校|大学|学院|公司|单位)[:：\s]*[\u4e00-\u9fffA-Za-z0-9（）()·-]{2,30}"
        ),
    ),
    (
        "person_name",
        re.compile(r"(?:姓名|名字|我叫|我是|联系人)[:：\s]*[\u4e00-\u9fff]{2,4}"),
    ),
    (
        "third_party_identity",
        re.compile(
            r"(?:室友|同学|老师|辅导员|朋友|男朋友|女朋友)"
            r"(?:[:：\s]+|(?:叫|名叫|是))"
            r"[\u4e00-\u9fff·]{2,8}"
        ),
    ),
)


def redact_sensitive_identifiers(text: str) -> tuple[str, list[str]]:
    """Redact demo-sensitive identifiers and return the detected categories."""
    detected: list[str] = []
    redacted = text
    for label, pattern in REDACTION_PATTERNS:
        if pattern.search(redacted):
            redacted = pattern.sub(f"[redacted:{label}]", redacted)
            detected.append(label)
    return redacted, detected


def detect_sensitive_categories(text: str) -> list[str]:
    """Return deterministic sensitive identifier categories without redacting text."""
    return [label for label, pattern in REDACTION_PATTERNS if pattern.search(text)]


def redact_validated_candidates(
    text: str,
    candidates: list[tuple[str, str]],
) -> tuple[str, list[str]]:
    """Redact model-proposed exact spans after deterministic validation."""
    redacted = text
    detected: list[str] = []
    allowed_labels = {label for label, _ in REDACTION_PATTERNS}
    for raw_span, label in candidates[:8]:
        span = raw_span.strip()
        if (
            label not in allowed_labels
            or not 2 <= len(span) <= 80
            or span not in redacted
            or span.startswith("[redacted:")
        ):
            continue
        redacted = redacted.replace(span, f"[redacted:{label}]")
        if label not in detected:
            detected.append(label)
    return redacted, detected
