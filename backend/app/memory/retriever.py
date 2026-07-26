"""Consent-gated, explainable episodic-memory retrieval baselines."""

from datetime import datetime, timezone
import logging
import re

from app.memory.long_term_repository import LongTermMemoryRepository
from app.memory.settings_store import UserMemorySettingsRepository
from app.memory.token_estimator import ConservativeTokenEstimator, TokenEstimator
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryRecordStatus,
    MemoryRetrievalDiagnostics,
    MemoryRetrievalHit,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    MemoryRetrievalScore,
    MemoryRetrievalStrategy,
)
from app.privacy.redaction import detect_sensitive_categories


logger = logging.getLogger(__name__)
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
_PROHIBITED_PATTERNS = (
    re.compile(r"(?:诊断|确诊|患有).{0,12}(?:症|障碍|疾病)"),
    re.compile(r"(?:自杀|自伤|不想活|结束生命|伤害自己|伤害他人)"),
    re.compile(r"(?:system\s*prompt|developer\s*message|系统提示词|开发者消息)", re.I),
    re.compile(r"(?:忽略|覆盖|绕过).{0,12}(?:系统|安全|记忆).{0,8}(?:指令|规则|策略)"),
)


class EpisodicMemoryRetriever:
    """Retrieve a few safe memories while repositories enforce hard scope."""

    def __init__(
        self,
        *,
        repository: LongTermMemoryRepository,
        settings_repository: UserMemorySettingsRepository,
        token_estimator: TokenEstimator | None = None,
        context_token_budget: int = 256,
        candidate_limit: int = 50,
    ) -> None:
        self.repository = repository
        self.settings_repository = settings_repository
        self.token_estimator = token_estimator or ConservativeTokenEstimator()
        self.context_token_budget = min(max(context_token_budget, 128), 1024)
        self.candidate_limit = min(max(candidate_limit, 10), 100)

    def retrieve(
        self,
        request: MemoryRetrievalRequest,
        *,
        now: datetime | None = None,
        record_usage: bool = True,
    ) -> MemoryRetrievalResult:
        """Return bounded hits; provider or model output never controls filters."""
        timestamp = _as_utc(now or datetime.now(timezone.utc))
        consent = self.settings_repository.get(request.user_id).consent_state
        if not consent.consent_to_practice_summary:
            return _empty_result(
                request,
                token_budget=self.context_token_budget,
                consent_allowed=False,
            )
        statuses = (MemoryRecordStatus.ACTIVE,)
        if request.include_archived:
            statuses = (*statuses, MemoryRecordStatus.ARCHIVED)
        query_terms = (
            sql_query_terms(request.query, request.scenario_type.value if request.scenario_type else None)
            if request.strategy == MemoryRetrievalStrategy.SQL_TEXT
            else ()
        )
        candidates = self.repository.search_memory_candidates(
            user_id=request.user_id,
            statuses=statuses,
            memory_types=tuple(dict.fromkeys(request.allowed_memory_types)),
            scenario_type=(
                request.scenario_type.value if request.scenario_type else None
            ),
            require_scenario_match=(
                request.strategy != MemoryRetrievalStrategy.RECENT
            ),
            query_terms=query_terms,
            now=timestamp,
            limit=self.candidate_limit,
        )
        hits, eligible_count = rank_memory_candidates(
            request=request,
            candidates=candidates,
            now=timestamp,
            token_estimator=self.token_estimator,
            token_budget=self.context_token_budget,
        )
        audit_failed = False
        if hits and record_usage:
            try:
                self.repository.record_retrieval(
                    user_id=request.user_id,
                    memory_ids=tuple(hit.memory_id for hit in hits),
                    retrieved_at=timestamp,
                    reason_code="context_retrieval",
                )
            except Exception as error:
                audit_failed = True
                logger.warning(
                    "Memory retrieval audit failed: %s",
                    error.__class__.__name__,
                )
        estimated_tokens = sum(hit.estimated_tokens for hit in hits)
        return MemoryRetrievalResult(
            hits=hits,
            diagnostics=MemoryRetrievalDiagnostics(
                strategy=request.strategy,
                candidate_count=len(candidates),
                eligible_count=eligible_count,
                returned_count=len(hits),
                estimated_tokens=estimated_tokens,
                token_budget=self.context_token_budget,
                abstained=not hits,
                consent_allowed=True,
                audit_failed=audit_failed,
            ),
        )


def rank_memory_candidates(
    *,
    request: MemoryRetrievalRequest,
    candidates: list[EpisodicMemoryRecord],
    now: datetime,
    token_estimator: TokenEstimator,
    token_budget: int,
) -> tuple[list[MemoryRetrievalHit], int]:
    """Pure strategy comparison used by runtime and fixed offline evaluations."""
    timestamp = _as_utc(now)
    query_terms = lexical_terms(request.query)
    eligible: list[tuple[EpisodicMemoryRecord, MemoryRetrievalScore]] = []
    for record in candidates:
        if not candidate_is_eligible(
            record=record,
            request=request,
            now=timestamp,
            query_terms=query_terms,
        ):
            continue
        score = _score_record(
            record=record,
            request=request,
            query_terms=query_terms,
            now=timestamp,
        )
        if (
            request.strategy == MemoryRetrievalStrategy.SQL_TEXT
            and score.lexical < 0.08
        ):
            continue
        if (
            record.status == MemoryRecordStatus.ARCHIVED
            and (score.lexical < 0.18 or score.scenario < 1.0)
        ):
            continue
        eligible.append((record, score))
    eligible.sort(
        key=lambda item: (
            item[1].total,
            item[0].occurred_at,
            item[0].memory_id,
        ),
        reverse=True,
    )

    hits: list[MemoryRetrievalHit] = []
    used_tokens = 0
    for record, score in eligible:
        remaining = token_budget - used_tokens
        if remaining <= 0 or len(hits) >= request.limit:
            break
        summary, cost = fit_memory_summary(
            record.summary,
            memory_type=record.memory_type.value,
            remaining_tokens=remaining,
            estimator=token_estimator,
        )
        if summary is None:
            continue
        hits.append(
            MemoryRetrievalHit(
                memory_id=record.memory_id,
                memory_type=record.memory_type,
                summary=summary,
                scenario_type=record.scenario_type,
                status=record.status,
                occurred_at=record.occurred_at,
                score=score,
                estimated_tokens=cost,
            )
        )
        used_tokens += cost
    return hits, len(eligible)


def candidate_is_eligible(
    *,
    record: EpisodicMemoryRecord,
    request: MemoryRetrievalRequest,
    now: datetime,
    query_terms: set[str],
) -> bool:
    """Defense-in-depth eligibility independent of repository correctness."""
    allowed_statuses = {MemoryRecordStatus.ACTIVE}
    if request.include_archived:
        allowed_statuses.add(MemoryRecordStatus.ARCHIVED)
    if (
        record.user_id != request.user_id
        or record.status not in allowed_statuses
        or record.memory_type not in request.allowed_memory_types
        or (record.expires_at is not None and record.expires_at <= now)
    ):
        return False
    if request.strategy != MemoryRetrievalStrategy.RECENT:
        if (
            request.scenario_type is not None
            and record.scenario_type not in {None, request.scenario_type}
        ):
            return False
    if request.strategy == MemoryRetrievalStrategy.SQL_TEXT:
        record_terms = lexical_terms(
            record.summary,
            record.scenario_type.value if record.scenario_type else None,
        )
        if not query_terms.intersection(record_terms):
            return False
    if not _retrieval_safe(record.summary):
        return False
    if _conflicts_with_current(request.query, record.summary):
        return False
    return True


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
    del scenario_type  # Scenario is an independent hard filter, not lexical evidence.
    terms = lexical_terms(query)
    ranked = sorted(
        terms,
        key=lambda term: (
            len(term),
            term,
        ),
        reverse=True,
    )
    return tuple(ranked[:16])


def _score_record(
    *,
    record: EpisodicMemoryRecord,
    request: MemoryRetrievalRequest,
    query_terms: set[str],
    now: datetime,
) -> MemoryRetrievalScore:
    record_terms = lexical_terms(record.summary)
    lexical = (
        len(query_terms.intersection(record_terms)) / len(query_terms)
        if query_terms
        else 0.0
    )
    scenario = (
        1.0
        if request.scenario_type is not None
        and record.scenario_type == request.scenario_type
        else 0.35 if record.scenario_type is None else 0.0
    )
    age_days = max(0.0, (now - _as_utc(record.occurred_at)).total_seconds() / 86400)
    recency = max(0.0, 1.0 - age_days / 365.0)
    if record.last_retrieved_at is None:
        novelty = 1.0
    else:
        retrieval_age_days = max(
            0.0,
            (now - _as_utc(record.last_retrieved_at)).total_seconds() / 86400,
        )
        novelty = min(1.0, retrieval_age_days / 30.0)
    weights = {
        MemoryRetrievalStrategy.RECENT: (0.15, 0.10, 0.50, 0.10, 0.15),
        MemoryRetrievalStrategy.METADATA: (0.20, 0.35, 0.25, 0.10, 0.10),
        MemoryRetrievalStrategy.SQL_TEXT: (0.40, 0.25, 0.15, 0.10, 0.10),
    }[request.strategy]
    total = (
        lexical * weights[0]
        + scenario * weights[1]
        + recency * weights[2]
        + novelty * weights[3]
        + record.confidence * weights[4]
    )
    if record.status == MemoryRecordStatus.ARCHIVED:
        total *= 0.9
    return MemoryRetrievalScore(
        lexical=round(min(1.0, lexical), 6),
        scenario=round(scenario, 6),
        recency=round(recency, 6),
        novelty=round(novelty, 6),
        confidence=round(record.confidence, 6),
        total=round(min(1.0, total), 6),
    )


def _conflicts_with_current(current: str, stored: str) -> bool:
    current_negative = any(marker in current.casefold() for marker in _NEGATION_MARKERS)
    stored_negative = any(marker in stored.casefold() for marker in _NEGATION_MARKERS)
    if current_negative == stored_negative:
        return False
    current_terms = lexical_terms(current)
    stored_terms = lexical_terms(stored)
    overlap = current_terms.intersection(stored_terms)
    return len(overlap) >= 2


def _retrieval_safe(summary: str) -> bool:
    if detect_sensitive_categories(summary):
        return False
    return not any(pattern.search(summary) for pattern in _PROHIBITED_PATTERNS)


def fit_memory_summary(
    summary: str,
    *,
    memory_type: str,
    remaining_tokens: int,
    estimator: TokenEstimator,
) -> tuple[str | None, int]:
    prefix = f"{memory_type}:"
    full_cost = estimator.count(prefix + summary)
    if full_cost <= remaining_tokens:
        return summary, full_cost
    if remaining_tokens <= estimator.count(prefix + "…"):
        return None, 0
    low, high = 1, len(summary)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = summary[:middle].rstrip() + "…"
        if estimator.count(prefix + candidate) <= remaining_tokens:
            low = middle
        else:
            high = middle - 1
    truncated = summary[:low].rstrip() + "…"
    cost = estimator.count(prefix + truncated)
    return (truncated, cost) if cost <= remaining_tokens else (None, 0)


def _empty_result(
    request: MemoryRetrievalRequest,
    *,
    token_budget: int,
    consent_allowed: bool,
) -> MemoryRetrievalResult:
    return MemoryRetrievalResult(
        diagnostics=MemoryRetrievalDiagnostics(
            strategy=request.strategy,
            candidate_count=0,
            eligible_count=0,
            returned_count=0,
            estimated_tokens=0,
            token_budget=token_budget,
            abstained=True,
            consent_allowed=consent_allowed,
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("memory retrieval timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
