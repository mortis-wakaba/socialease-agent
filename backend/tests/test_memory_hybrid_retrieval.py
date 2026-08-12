"""Unit contracts for indexed hybrid episodic-memory candidate recall."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from app.memory.embedding import (
    create_memory_embedding_provider,
    memory_retrieval_mode,
)
from app.memory.retriever import EpisodicMemoryRetriever
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryRecordStatus,
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
    MemorySourceType,
    MemoryType,
)
from app.models_memory import UserConsentState, UserMemorySettings


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _record(memory_id: str, summary: str, *, user_id: str = "user-1") -> EpisodicMemoryRecord:
    occurred_at = NOW - timedelta(days=180)
    return EpisodicMemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary=summary,
        source_type=MemorySourceType.USER_CONFIRMED,
        source_id=f"source-{memory_id}",
        evidence_type=MemoryEvidenceType.USER_CONFIRMED,
        confidence=0.9,
        status=MemoryRecordStatus.ACTIVE,
        occurred_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
        expires_at=NOW + timedelta(days=180),
        consent_version="practice-summary-v1",
        content_hash=hashlib.sha256(summary.encode()).hexdigest(),
        idempotency_key=hashlib.sha256(memory_id.encode()).hexdigest(),
    )


class _SettingsRepository:
    async def get(self, user_id: str) -> UserMemorySettings:
        return UserMemorySettings(
            user_id=user_id,
            consent_state=UserConsentState(consent_to_practice_summary=True),
        )


class _EmbeddingProvider:
    provider_name = "test"
    model_name = "test-embedding"
    model_revision = "test-revision"
    dimensions = 3
    model_size_mb = 0.0

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.queries.extend(texts)
        return [[1.0, 0.0, 0.0]]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FailingEmbeddingProvider(_EmbeddingProvider):
    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        raise TimeoutError("embedding timed out")


class _Repository:
    def __init__(
        self,
        *,
        fts: list[EpisodicMemoryRecord] | Exception,
        vector: list[tuple[EpisodicMemoryRecord, float]] | Exception,
        recent: list[EpisodicMemoryRecord] | None = None,
    ) -> None:
        self.fts = fts
        self.vector = vector
        self.recent = recent or []
        self.fts_calls = 0
        self.vector_calls = 0
        self.recent_calls = 0
        self.fts_query_terms: tuple[str, ...] = ()

    async def search_memory_fts_candidates(self, **kwargs):
        self.fts_calls += 1
        self.fts_query_terms = kwargs["query_terms"]
        assert kwargs["user_id"] == "user-1"
        if isinstance(self.fts, Exception):
            raise self.fts
        return self.fts

    async def search_memory_vector_candidates(self, **kwargs):
        self.vector_calls += 1
        assert kwargs["embedding_revision"] == "test-revision"
        assert kwargs["query_vector"] == [1.0, 0.0, 0.0]
        assert kwargs["minimum_similarity"] == 0.45
        if isinstance(self.vector, Exception):
            raise self.vector
        return self.vector

    async def search_memory_candidates(self, **kwargs):
        self.recent_calls += 1
        return self.recent

    async def record_retrieval(self, **kwargs) -> int:
        return len(kwargs["memory_ids"])


def _request() -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        user_id="user-1",
        query="我想练习在小组讨论中更自然地加入话题",
        allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
        strategy=MemoryRetrievalStrategy.SQL_TEXT,
    )


def test_dense_only_mode_enables_embedding_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOCIALEASE_MEMORY_RETRIEVAL_MODE", "dense_only")

    assert memory_retrieval_mode() == "dense_only"
    assert create_memory_embedding_provider() is not None


def test_non_vector_mode_does_not_create_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_MEMORY_RETRIEVAL_MODE", "postgres_fts")

    assert create_memory_embedding_provider() is None


def _retriever(repository: _Repository) -> EpisodicMemoryRetriever:
    return EpisodicMemoryRetriever(
        repository=repository,
        settings_repository=_SettingsRepository(),
        retrieval_mode="hybrid",
        embedding_provider=_EmbeddingProvider(),
        context_token_budget=128,
    )


def _retriever_with_provider(
    repository: _Repository,
    provider: _EmbeddingProvider,
) -> EpisodicMemoryRetriever:
    return EpisodicMemoryRetriever(
        repository=repository,
        settings_repository=_SettingsRepository(),
        retrieval_mode="hybrid",
        embedding_provider=provider,
        context_token_budget=128,
    )


def _dense_retriever(
    repository: _Repository,
    provider: _EmbeddingProvider | None = None,
) -> EpisodicMemoryRetriever:
    return EpisodicMemoryRetriever(
        repository=repository,
        settings_repository=_SettingsRepository(),
        retrieval_mode="dense_only",
        embedding_provider=provider or _EmbeddingProvider(),
        context_token_budget=128,
    )


@pytest.mark.anyio
async def test_hybrid_recall_fuses_both_indexes_without_recent_window() -> None:
    shared = _record("shared", "之前在小组里先回应上一位同学，再补充自己的观点会更自然。")
    lexical = _record("lexical", "小组讨论时可以先用一句简短回应加入话题。")
    semantic = _record("semantic", "先接住别人刚才的观点，再说自己的想法。")
    repository = _Repository(
        fts=[lexical, shared],
        vector=[(semantic, 0.82), (shared, 0.78)],
    )

    result = await _retriever(repository).retrieve(
        _request(), now=NOW, record_usage=False
    )

    assert repository.fts_calls == 1
    assert repository.vector_calls == 1
    assert repository.recent_calls == 0
    assert result.diagnostics.strategy == MemoryRetrievalStrategy.HYBRID
    assert result.diagnostics.candidate_count == 3
    assert {hit.memory_id for hit in result.hits} == {"shared", "lexical", "semantic"}
    scores = {hit.memory_id: hit.score.fusion for hit in result.hits}
    assert scores["shared"] > scores["lexical"]
    assert scores["shared"] > scores["semantic"]


@pytest.mark.anyio
async def test_dense_only_recall_uses_vector_index_without_fts_or_recent() -> None:
    first = _record("first", "先接住别人刚才的观点，再说自己的想法。")
    second = _record("second", "小组讨论时可以先用一句简短回应加入话题。")
    repository = _Repository(
        fts=AssertionError("dense-only must not call FTS"),
        vector=[(first, 0.91), (second, 0.82)],
    )

    result = await _dense_retriever(repository).retrieve(
        _request(), now=NOW, record_usage=False
    )

    assert repository.fts_calls == 0
    assert repository.vector_calls == 1
    assert repository.recent_calls == 0
    assert result.diagnostics.strategy == MemoryRetrievalStrategy.DENSE_ONLY
    assert result.diagnostics.candidate_count == 2
    assert {hit.memory_id for hit in result.hits} == {"first", "second"}


@pytest.mark.anyio
async def test_dense_only_failure_degrades_to_fts() -> None:
    lexical = _record("lexical", "小组讨论时先用一句回应加入话题。")
    repository = _Repository(
        fts=[lexical],
        vector=RuntimeError("vector unavailable"),
    )

    result = await _dense_retriever(repository).retrieve(
        _request(), now=NOW, record_usage=False
    )

    assert repository.fts_calls == 1
    assert repository.vector_calls == 1
    assert repository.recent_calls == 0
    assert result.diagnostics.strategy == MemoryRetrievalStrategy.POSTGRES_FTS
    assert [hit.memory_id for hit in result.hits] == ["lexical"]


@pytest.mark.anyio
async def test_dense_only_and_fts_failures_use_bounded_recent_fallback() -> None:
    fallback = _record("fallback", "小组讨论时先用一句回应加入话题。")
    repository = _Repository(
        fts=RuntimeError("fts unavailable"),
        vector=RuntimeError("vector unavailable"),
        recent=[fallback],
    )

    result = await _dense_retriever(repository).retrieve(
        _request(), now=NOW, record_usage=False
    )

    assert repository.fts_calls == 1
    assert repository.vector_calls == 1
    assert repository.recent_calls == 1
    assert result.diagnostics.strategy == MemoryRetrievalStrategy.SQL_TEXT
    assert [hit.memory_id for hit in result.hits] == ["fallback"]


@pytest.mark.anyio
async def test_vector_failure_degrades_to_fts_without_recent_window() -> None:
    lexical = _record("lexical", "小组讨论时可以先用一句简短回应加入话题。")
    repository = _Repository(
        fts=[lexical],
        vector=RuntimeError("vector unavailable"),
    )

    result = await _retriever(repository).retrieve(
        _request(), now=NOW, record_usage=False
    )

    assert repository.recent_calls == 0
    assert result.diagnostics.strategy == MemoryRetrievalStrategy.POSTGRES_FTS
    assert [hit.memory_id for hit in result.hits] == ["lexical"]


@pytest.mark.anyio
async def test_embedding_failure_degrades_to_fts_without_recent_window() -> None:
    lexical = _record("lexical", "小组讨论时可以先用一句简短回应加入话题。")
    repository = _Repository(fts=[lexical], vector=[])

    result = await _retriever_with_provider(
        repository,
        _FailingEmbeddingProvider(),
    ).retrieve(_request(), now=NOW, record_usage=False)

    assert repository.fts_calls == 1
    assert repository.vector_calls == 0
    assert repository.recent_calls == 0
    assert result.diagnostics.strategy == MemoryRetrievalStrategy.POSTGRES_FTS
    assert [hit.memory_id for hit in result.hits] == ["lexical"]


@pytest.mark.anyio
async def test_both_index_failures_use_bounded_recent_fallback() -> None:
    fallback = _record("fallback", "小组讨论时先用一句回应加入话题。")
    repository = _Repository(
        fts=RuntimeError("fts unavailable"),
        vector=RuntimeError("vector unavailable"),
        recent=[fallback],
    )

    result = await _retriever(repository).retrieve(
        _request(), now=NOW, record_usage=False
    )

    assert repository.recent_calls == 1
    assert result.diagnostics.strategy == MemoryRetrievalStrategy.SQL_TEXT
    assert [hit.memory_id for hit in result.hits] == ["fallback"]


@pytest.mark.anyio
async def test_hybrid_recall_reapplies_owner_hard_filter() -> None:
    leaked = _record(
        "other-user",
        "小组讨论时可以先回应上一位同学。",
        user_id="user-2",
    )
    repository = _Repository(fts=[leaked], vector=[(leaked, 0.99)])

    result = await _retriever(repository).retrieve(
        _request(), now=NOW, record_usage=False
    )

    assert result.hits == []
    assert result.diagnostics.eligible_count == 0
