"""Read-only, policy-scoped access to the existing episodic memory retriever."""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from app.memory.active_memory_assembler import episodic_types_for_skill
from app.memory.control_intent import should_bypass_episodic_retrieval
from app.memory.retriever import EpisodicMemoryRetriever
from app.models import RiskLevel
from app.models_long_term_memory import (
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
)
from app.models_memory_tool import (
    MemoryToolDecisionReason,
    MemoryToolEvidence,
    MemoryToolRunResult,
    MemoryEvidenceSelectorResult,
)


class MemoryEvidenceSelectorProtocol(Protocol):
    """Minimal boundary for optional set-level evidence selection."""

    async def select(
        self,
        *,
        historical_question: str,
        current_request: str,
        candidates: list[MemoryToolEvidence],
    ) -> MemoryEvidenceSelectorResult: ...


class MemorySearchService:
    """Execute at most one owner-scoped indexed search for an approved tool call."""

    def __init__(
        self,
        retriever: EpisodicMemoryRetriever,
        *,
        evidence_selector: MemoryEvidenceSelectorProtocol | None = None,
    ) -> None:
        self._retriever = retriever
        self._evidence_selector = evidence_selector

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        current_request: str,
        skill_name: str,
        risk_level: RiskLevel,
    ) -> MemoryToolRunResult:
        """Return bounded evidence without allowing model-controlled scope fields."""
        if risk_level in {RiskLevel.HIGH, RiskLevel.CRISIS}:
            return MemoryToolRunResult(
                decision_action="search_practice_memory",
                decision_reason=(
                    MemoryToolDecisionReason.EXPLICIT_CROSS_SESSION_HISTORY
                ),
                outcome="blocked_risk",
            )
        if should_bypass_episodic_retrieval(current_request):
            return MemoryToolRunResult(
                decision_action="search_practice_memory",
                decision_reason=(
                    MemoryToolDecisionReason.EXPLICIT_CROSS_SESSION_HISTORY
                ),
                outcome="blocked_control_intent",
            )
        allowed_types = episodic_types_for_skill(skill_name)
        if not allowed_types:
            return MemoryToolRunResult(
                decision_action="search_practice_memory",
                decision_reason=(
                    MemoryToolDecisionReason.EXPLICIT_CROSS_SESSION_HISTORY
                ),
                outcome="abstained",
            )
        result = await self._retriever.retrieve(
            MemoryRetrievalRequest(
                user_id=user_id,
                query=query,
                allowed_memory_types=list(allowed_types),
                strategy=MemoryRetrievalStrategy.SQL_TEXT,
            )
        )
        candidates = [
            MemoryToolEvidence(
                evidence_id=_evidence_id(hit.memory_id),
                memory_type=hit.memory_type,
                safe_summary=hit.summary,
                relevance_score=hit.score.total,
            )
            for hit in result.hits
        ][:3]
        if self._evidence_selector is None:
            evidence = candidates
            selector_outcome = "not_run"
            selector_candidate_count = len(candidates)
            selector_question_direction = None
        else:
            selection = await self._evidence_selector.select(
                historical_question=query,
                current_request=current_request,
                candidates=candidates,
            )
            evidence = selection.evidence
            selector_outcome = selection.outcome
            selector_candidate_count = selection.candidate_count
            selector_question_direction = selection.question_direction
        return MemoryToolRunResult(
            decision_action="search_practice_memory",
            decision_reason=MemoryToolDecisionReason.EXPLICIT_CROSS_SESSION_HISTORY,
            outcome="returned" if evidence else "abstained",
            evidence=evidence,
            selector_outcome=selector_outcome,
            selector_candidate_count=selector_candidate_count,
            selector_question_direction=selector_question_direction,
        )


def _evidence_id(memory_id: str) -> str:
    return f"mem_ev_{sha256(memory_id.encode()).hexdigest()[:16]}"
