"""Database-independent hard safety and ownership filters for memory retrieval."""

from collections import Counter
from datetime import datetime, timezone
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field

from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryRecordStatus,
    MemoryRetrievalRequest,
)
from app.privacy.redaction import detect_sensitive_categories


_PROHIBITED_PATTERNS = (
    re.compile(r"(?:诊断|确诊|患有).{0,12}(?:症|障碍|疾病)"),
    re.compile(r"(?:自杀|自伤|不想活|结束生命|伤害自己|伤害他人)"),
    re.compile(r"(?:system\s*prompt|developer\s*message|系统提示词|开发者消息)", re.I),
    re.compile(r"(?:忽略|覆盖|绕过).{0,12}(?:系统|安全|记忆).{0,8}(?:指令|规则|策略)"),
)


class MemoryHardFilterReason(str, Enum):
    """Content-free reason for rejecting a retrieval candidate."""

    ALLOWED = "allowed"
    WRONG_USER = "wrong_user"
    DISALLOWED_STATUS = "disallowed_status"
    DISALLOWED_TYPE = "disallowed_type"
    EXPIRED = "expired"
    SENSITIVE_CONTENT = "sensitive_content"
    PROHIBITED_CONTENT = "prohibited_content"


class MemoryHardFilterReport(BaseModel):
    """Aggregate filter telemetry that never copies query or memory text."""

    model_config = ConfigDict(extra="forbid")

    input_count: int = Field(ge=0)
    allowed_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rejected_by_reason: dict[MemoryHardFilterReason, int] = Field(
        default_factory=dict
    )


class MemoryHardFilter:
    """Apply the same non-negotiable boundary before every recall channel."""

    def evaluate(
        self,
        *,
        record: EpisodicMemoryRecord,
        request: MemoryRetrievalRequest,
        now: datetime,
    ) -> MemoryHardFilterReason:
        """Return one stable rejection reason, ordered from scope to content."""
        timestamp = _as_utc(now)
        allowed_statuses = {MemoryRecordStatus.ACTIVE}
        if request.include_archived:
            allowed_statuses.add(MemoryRecordStatus.ARCHIVED)
        if record.user_id != request.user_id:
            return MemoryHardFilterReason.WRONG_USER
        if record.status not in allowed_statuses:
            return MemoryHardFilterReason.DISALLOWED_STATUS
        if record.memory_type not in request.allowed_memory_types:
            return MemoryHardFilterReason.DISALLOWED_TYPE
        if record.expires_at is not None and _as_utc(record.expires_at) <= timestamp:
            return MemoryHardFilterReason.EXPIRED
        if detect_sensitive_categories(record.summary):
            return MemoryHardFilterReason.SENSITIVE_CONTENT
        if any(pattern.search(record.summary) for pattern in _PROHIBITED_PATTERNS):
            return MemoryHardFilterReason.PROHIBITED_CONTENT
        return MemoryHardFilterReason.ALLOWED

    def filter(
        self,
        *,
        records: list[EpisodicMemoryRecord],
        request: MemoryRetrievalRequest,
        now: datetime,
    ) -> tuple[list[EpisodicMemoryRecord], MemoryHardFilterReport]:
        """Return eligible records and content-free aggregate diagnostics."""
        allowed: list[EpisodicMemoryRecord] = []
        reasons: Counter[MemoryHardFilterReason] = Counter()
        for record in records:
            reason = self.evaluate(record=record, request=request, now=now)
            if reason == MemoryHardFilterReason.ALLOWED:
                allowed.append(record)
            else:
                reasons[reason] += 1
        return allowed, MemoryHardFilterReport(
            input_count=len(records),
            allowed_count=len(allowed),
            rejected_count=len(records) - len(allowed),
            rejected_by_reason=dict(reasons),
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("memory retrieval timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
