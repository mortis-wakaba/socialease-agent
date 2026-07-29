"""Explicit abstention policy for reranked long-term memories."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.memory.hard_filter import MemoryHardFilter, MemoryHardFilterReason
from app.memory.reranker import RerankedMemory
from app.memory.text_semantics import lexical_terms, memories_conflict
from app.models_long_term_memory import MemoryRetrievalRequest


class MemoryAbstentionReason(str, Enum):
    """Stable, content-free reason for returning no long-term memory."""

    NONE = "none"
    NO_CANDIDATES = "no_candidates"
    LOW_TOP_SCORE = "low_top_score"
    AMBIGUOUS_CONFLICT = "ambiguous_conflict"
    REQUIRED_CONTEXT_MISSING = "required_context_missing"
    CURRENT_QUERY_CONFLICT = "current_query_conflict"
    RERANKER_UNAVAILABLE = "reranker_unavailable"


class MemoryAbstentionDiagnostics(BaseModel):
    """Decision evidence without query or memory content."""

    model_config = ConfigDict(extra="forbid")

    reason: MemoryAbstentionReason
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0, le=3)
    top_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_margin: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_score: float = Field(ge=0.0, le=1.0)
    minimum_conflict_margin: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class MemoryAbstentionDecision:
    """Selected internal candidates or an explicit normal abstention."""

    selected: list[RerankedMemory]
    diagnostics: MemoryAbstentionDiagnostics


class MemoryAbstentionPolicy:
    """Fail closed on weak, conflicting or scope-mismatched retrieval."""

    def __init__(
        self,
        *,
        minimum_score: float = 0.45,
        minimum_conflict_margin: float = 0.03,
        hard_filter: MemoryHardFilter | None = None,
    ) -> None:
        self.minimum_score = min(max(minimum_score, 0.0), 1.0)
        self.minimum_conflict_margin = min(
            max(minimum_conflict_margin, 0.0),
            1.0,
        )
        self.hard_filter = hard_filter or MemoryHardFilter()

    def decide(
        self,
        *,
        request: MemoryRetrievalRequest,
        candidates: list[RerankedMemory],
        now,
    ) -> MemoryAbstentionDecision:
        """Choose bounded candidates only when confidence and constraints hold."""
        if not candidates:
            return self._abstain(
                reason=MemoryAbstentionReason.NO_CANDIDATES,
                candidate_count=0,
            )
        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        margin = (
            max(0.0, top.final_score - second.final_score)
            if second is not None
            else None
        )
        if top.final_score < self.minimum_score:
            return self._abstain(
                reason=MemoryAbstentionReason.LOW_TOP_SCORE,
                candidate_count=len(candidates),
                top_score=top.final_score,
                score_margin=margin,
            )
        if (
            self.hard_filter.evaluate(
                record=top.recalled.record,
                request=request,
                now=now,
            )
            != MemoryHardFilterReason.ALLOWED
        ):
            return self._abstain(
                reason=MemoryAbstentionReason.CURRENT_QUERY_CONFLICT,
                candidate_count=len(candidates),
                top_score=top.final_score,
                score_margin=margin,
            )
        if (
            second is not None
            and margin is not None
            and margin < self.minimum_conflict_margin
            and memories_conflict(
                top.recalled.record.summary,
                second.recalled.record.summary,
            )
        ):
            return self._abstain(
                reason=MemoryAbstentionReason.AMBIGUOUS_CONFLICT,
                candidate_count=len(candidates),
                top_score=top.final_score,
                score_margin=margin,
            )
        if not _required_context_is_covered(request, top):
            return self._abstain(
                reason=MemoryAbstentionReason.REQUIRED_CONTEXT_MISSING,
                candidate_count=len(candidates),
                top_score=top.final_score,
                score_margin=margin,
            )
        selected = [
            candidate
            for candidate in candidates
            if candidate.final_score >= self.minimum_score
            and self.hard_filter.evaluate(
                record=candidate.recalled.record,
                request=request,
                now=now,
            )
            == MemoryHardFilterReason.ALLOWED
        ][: request.limit]
        return MemoryAbstentionDecision(
            selected=selected,
            diagnostics=MemoryAbstentionDiagnostics(
                reason=MemoryAbstentionReason.NONE,
                candidate_count=len(candidates),
                selected_count=len(selected),
                top_score=top.final_score,
                score_margin=margin,
                minimum_score=self.minimum_score,
                minimum_conflict_margin=self.minimum_conflict_margin,
            ),
        )

    def reranker_unavailable(
        self,
        *,
        candidate_count: int,
    ) -> MemoryAbstentionDecision:
        """Represent model failure explicitly instead of falling back silently."""
        return self._abstain(
            reason=MemoryAbstentionReason.RERANKER_UNAVAILABLE,
            candidate_count=candidate_count,
        )

    def _abstain(
        self,
        *,
        reason: MemoryAbstentionReason,
        candidate_count: int,
        top_score: float | None = None,
        score_margin: float | None = None,
    ) -> MemoryAbstentionDecision:
        return MemoryAbstentionDecision(
            selected=[],
            diagnostics=MemoryAbstentionDiagnostics(
                reason=reason,
                candidate_count=candidate_count,
                selected_count=0,
                top_score=top_score,
                score_margin=score_margin,
                minimum_score=self.minimum_score,
                minimum_conflict_margin=self.minimum_conflict_margin,
            ),
        )


def _required_context_is_covered(
    request: MemoryRetrievalRequest,
    candidate: RerankedMemory,
) -> bool:
    record = candidate.recalled.record
    continuity = (
        request.practice_thread_id is not None
        and record.practice_thread_id == request.practice_thread_id
    ) or (
        request.scenario_id is not None
        and record.scenario_id == request.scenario_id
    )
    skills = set(request.skill_codes)
    skill_match = bool(skills.intersection(record.skill_codes))
    scenario_match = (
        request.scenario_type is not None
        and record.scenario_type == request.scenario_type
    )
    lexical_match = bool(
        lexical_terms(request.query).intersection(lexical_terms(record.summary))
    )
    has_structured_requirement = bool(
        request.practice_thread_id
        or request.scenario_id
        or request.skill_codes
        or request.scenario_type
    )
    return (
        not has_structured_requirement
        or continuity
        or skill_match
        or scenario_match
        or lexical_match
    )
