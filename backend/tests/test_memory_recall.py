"""Contracts for four-route long-term-memory recall."""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import hashlib

from app.memory.recall import (
    DeterministicMemoryQueryExpander,
    MemoryRecallChannel,
    MultiRouteMemoryRecall,
)
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryRetrievalRequest,
    MemorySourceType,
    MemoryType,
)
from app.models_scenario import SocialSkillCode


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


class _KeywordEmbedder:
    provider_name = "test"
    model_name = "keyword"
    model_revision = "1"
    dimensions = 3
    model_size_mb = 0.0

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        return [
            float("表达" in text or "发言" in text or "观点" in text),
            float("边界" in text or "拒绝" in text),
            float("提问" in text or "询问" in text),
        ]


def _record(
    memory_id: str,
    summary: str,
    *,
    user_id: str = "user_a",
    scenario_type: str | None = None,
    practice_thread_id: str | None = None,
    skill_codes: list[SocialSkillCode] | None = None,
) -> EpisodicMemoryRecord:
    digest = hashlib.sha256(summary.encode()).hexdigest()
    return EpisodicMemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary=summary,
        scenario_type=scenario_type,
        practice_thread_id=practice_thread_id,
        skill_codes=skill_codes or [],
        source_type=MemorySourceType.USER_CONFIRMED,
        source_id="demo_source",
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=0.95,
        occurred_at=NOW - timedelta(days=2),
        expires_at=NOW + timedelta(days=30),
        consent_version="demo-v1",
        content_hash=digest,
        idempotency_key=digest,
    )


def _request() -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        user_id="user_a",
        query="小组发言时怎样更简短地表达观点？",
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
        scenario_type="group_discussion",
        practice_thread_id="thread_current",
        skill_codes=[SocialSkillCode.ASSERTIVE_EXPRESSION],
    )


def test_query_expansion_is_bounded_and_preserves_original_constraints() -> None:
    expanded = DeterministicMemoryQueryExpander().expand(_request())

    assert 1 < len(expanded.variants) <= 4
    assert expanded.variants[0] == _request().query
    assert all(_request().query in variant for variant in expanded.variants)


def test_four_routes_are_fused_by_memory_id_after_hard_filtering() -> None:
    target = _record(
        "target",
        "讨论时先用一句话说明核心观点，再补充理由。",
        scenario_type="group_discussion",
        practice_thread_id="thread_current",
        skill_codes=[SocialSkillCode.ASSERTIVE_EXPRESSION],
    )
    lexical = _record(
        "lexical",
        "小组发言时可以先写下观点关键词。",
        scenario_type="group_discussion",
    )
    leaked = _record(
        "other_user",
        "小组发言时简短表达观点。",
        user_id="user_b",
        scenario_type="group_discussion",
    )

    result = MultiRouteMemoryRecall(
        embedder=_KeywordEmbedder(),
        per_channel_limit=10,
    ).recall(
        request=_request(),
        records=[target, lexical, leaked],
        now=NOW,
    )

    assert [item.record.memory_id for item in result.candidates][:2] == [
        "target",
        "lexical",
    ]
    assert result.diagnostics.filtered.rejected_count == 1
    assert result.diagnostics.union_count == 2
    assert set(result.diagnostics.recalled_by_channel) == set(MemoryRecallChannel)
    target_channels = result.candidates[0].channel_ranks
    assert MemoryRecallChannel.DENSE in target_channels
    assert MemoryRecallChannel.METADATA in target_channels
    assert MemoryRecallChannel.MULTI_QUERY in target_channels


def test_recall_diagnostics_do_not_copy_query_or_summaries() -> None:
    request = _request()
    record = _record(
        "target",
        "讨论时先用一句话说明核心观点。",
        scenario_type="group_discussion",
    )

    result = MultiRouteMemoryRecall(embedder=_KeywordEmbedder()).recall(
        request=request,
        records=[record],
        now=NOW,
    )
    serialized = result.diagnostics.model_dump_json()

    assert request.query not in serialized
    assert record.summary not in serialized
