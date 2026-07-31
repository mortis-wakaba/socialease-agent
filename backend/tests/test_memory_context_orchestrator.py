"""Tests for the shared stable and episodic memory orchestration boundary."""

from datetime import datetime, timezone

import pytest

from app.memory.context_orchestrator import MemoryContextOrchestrator
from app.memory.settings_store import InMemoryUserMemorySettingsRepository
from app.models_long_term_memory import (
    MemoryRecordStatus,
    MemoryRetrievalDiagnostics,
    MemoryRetrievalHit,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    MemoryRetrievalScore,
    MemoryRetrievalStrategy,
    MemoryType,
)
from app.models_memory import UserConsentState, UserPracticeSummary


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)


class ProfileRepository:
    """Return one privacy-minimized summary without external persistence."""

    async def get_summary(self, user_id: str) -> UserPracticeSummary:
        del user_id
        return UserPracticeSummary()


class CapturingRetriever:
    """Capture the application-owned retrieval request and return fixed hits."""

    def __init__(self) -> None:
        self.requests: list[MemoryRetrievalRequest] = []

    async def retrieve(
        self,
        request: MemoryRetrievalRequest,
    ) -> MemoryRetrievalResult:
        self.requests.append(request)
        hits = [
            MemoryRetrievalHit(
                memory_id="memory-1",
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary="先写下一句开场有帮助",
                status=MemoryRecordStatus.ACTIVE,
                occurred_at=NOW,
                score=MemoryRetrievalScore(
                    lexical=0.8,
                    scenario=0.0,
                    recency=1.0,
                    novelty=1.0,
                    confidence=0.9,
                    total=0.9,
                ),
                estimated_tokens=12,
            )
        ]
        return MemoryRetrievalResult(
            hits=hits,
            diagnostics=MemoryRetrievalDiagnostics(
                strategy=request.strategy,
                candidate_count=1,
                eligible_count=1,
                returned_count=1,
                estimated_tokens=12,
                token_budget=256,
                abstained=False,
                consent_allowed=True,
            ),
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_general_support_uses_allowlist_and_sql_text_baseline() -> None:
    settings = InMemoryUserMemorySettingsRepository()
    await settings.save(
        user_id="owner",
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    retriever = CapturingRetriever()
    orchestrator = MemoryContextOrchestrator(
        user_profile_repository=ProfileRepository(),  # type: ignore[arg-type]
        settings_repository=settings,
        episodic_retriever=retriever,  # type: ignore[arg-type]
    )

    packet = await orchestrator.assemble(
        user_id="owner",
        skill_name="general_support_skill",
        current_request="今天想练习开口",
    )

    assert packet.episodic_memories == [
        "helpful_strategy: 先写下一句开场有帮助"
    ]
    request = retriever.requests[0]
    assert request.strategy == MemoryRetrievalStrategy.SQL_TEXT
    assert set(request.allowed_memory_types) == {
        MemoryType.HELPFUL_STRATEGY,
        MemoryType.PRACTICE_EXPERIENCE,
    }


@pytest.mark.anyio
async def test_skill_without_episodic_allowlist_skips_retrieval() -> None:
    settings = InMemoryUserMemorySettingsRepository()
    retriever = CapturingRetriever()
    orchestrator = MemoryContextOrchestrator(
        user_profile_repository=ProfileRepository(),  # type: ignore[arg-type]
        settings_repository=settings,
        episodic_retriever=retriever,  # type: ignore[arg-type]
    )

    packet = await orchestrator.assemble(
        user_id="owner",
        skill_name="support_resource_rag_skill",
        current_request="查找公开资源",
    )

    assert packet.episodic_memories == []
    assert retriever.requests == []
