"""Deterministically assemble stable, working, and episodic memory."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from app.memory.retriever import lexical_terms
from app.memory.token_estimator import ConservativeTokenEstimator, TokenEstimator
from app.models_active_memory import (
    ActiveMemoryDropReason,
    ActiveMemoryLayer,
    ActiveMemoryPacket,
    ActiveMemorySelectionRecord,
)
from app.models_context import ContextValueSource, SkillContextProjection
from app.models_long_term_memory import (
    MemoryRetrievalHit,
    MemoryRetrievalResult,
    MemoryType,
)
from app.models_session_context import DurableCheckpointContext


_SKILL_EPISODIC_ALLOWLIST: dict[str, frozenset[MemoryType]] = {
    "roleplay_skill": frozenset(
        {
            MemoryType.HELPFUL_STRATEGY,
            MemoryType.PRACTICE_EXPERIENCE,
            MemoryType.PRACTICE_MILESTONE,
        }
    ),
    "exposure_planning_skill": frozenset(
        {
            MemoryType.HELPFUL_STRATEGY,
            MemoryType.PRACTICE_EXPERIENCE,
            MemoryType.PRACTICE_MILESTONE,
        }
    ),
    "worksheet_skill": frozenset({MemoryType.HELPFUL_STRATEGY}),
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


class ActiveMemoryAssembler:
    """Apply application-owned allowlists, precedence, scope, and one budget."""

    def __init__(
        self,
        *,
        token_estimator: TokenEstimator | None = None,
        token_budget: int = 512,
        max_episodic_items: int = 3,
    ) -> None:
        self.token_estimator = token_estimator or ConservativeTokenEstimator()
        self.token_budget = min(max(token_budget, 128), 4096)
        self.max_episodic_items = min(max(max_episodic_items, 1), 3)

    def assemble(
        self,
        *,
        user_id: str,
        skill_context: SkillContextProjection,
        current_request: str,
        durable_checkpoint: DurableCheckpointContext | None = None,
        memory_retrieval: MemoryRetrievalResult | None = None,
        retrieval_user_id: str | None = None,
        consent_allowed: bool = True,
        assembled_at: datetime | None = None,
    ) -> ActiveMemoryPacket:
        """Build one bounded packet without letting model output choose scope."""
        timestamp = assembled_at or datetime.now(timezone.utc)
        selections: list[ActiveMemorySelectionRecord] = []
        stable, used_tokens = self._select_stable(
            user_id=user_id,
            projection=skill_context,
            selections=selections,
        )
        working = self._select_working(
            user_id=user_id,
            checkpoint=durable_checkpoint,
            used_tokens=used_tokens,
            selections=selections,
        )
        if working is not None:
            used_tokens += working.estimated_tokens
        episodic, episodic_tokens = self._select_episodic(
            user_id=user_id,
            skill_name=skill_context.skill_name,
            current_request=current_request,
            result=memory_retrieval,
            retrieval_user_id=retrieval_user_id,
            consent_allowed=consent_allowed,
            used_tokens=used_tokens,
            selections=selections,
        )
        return ActiveMemoryPacket(
            skill_name=skill_context.skill_name,
            stable_memory=stable,
            working_memory=working,
            episodic_memories=episodic,
            selections=selections,
            estimated_tokens=used_tokens + episodic_tokens,
            token_budget=self.token_budget,
            assembled_at=timestamp,
        )

    def _select_stable(
        self,
        *,
        user_id: str,
        projection: SkillContextProjection,
        selections: list[ActiveMemorySelectionRecord],
    ) -> tuple[SkillContextProjection, int]:
        values: dict[str, object] = {}
        metadata = {}
        ordered_fields = sorted(
            projection.selected_fields,
            key=lambda field: (
                0
                if ContextValueSource.CURRENT_REQUEST
                in projection.field_metadata[field].sources
                else 1,
                field,
            ),
        )
        used = 0
        drop_reasons = dict(projection.drop_reasons)
        for field in ordered_fields:
            value = projection.values.get(field)
            cost = self.token_estimator.count(
                json.dumps({field: value}, ensure_ascii=False, default=str)
            )
            selected = used + cost <= self.token_budget
            if selected:
                values[field] = value
                metadata[field] = projection.field_metadata[field]
                used += cost
            else:
                drop_reasons[field] = ActiveMemoryDropReason.TOKEN_BUDGET.value
            field_metadata = projection.field_metadata[field]
            selections.append(
                ActiveMemorySelectionRecord(
                    memory_id_hash=_hash_id(f"{user_id}:stable:{field}"),
                    memory_layer=ActiveMemoryLayer.STABLE,
                    memory_type=field,
                    source_type="+".join(
                        source.value for source in field_metadata.sources
                    )
                    or "unknown",
                    confidence=field_metadata.confidence.value,
                    selected=selected,
                    drop_reason=(
                        None
                        if selected
                        else ActiveMemoryDropReason.TOKEN_BUDGET
                    ),
                    estimated_tokens=cost if selected else 0,
                )
            )
        return (
            projection.model_copy(
                update={
                    "values": values,
                    "selected_fields": sorted(values),
                    "field_metadata": metadata,
                    "dropped_fields": sorted(drop_reasons),
                    "drop_reasons": drop_reasons,
                },
                deep=True,
            ),
            used,
        )

    def _select_working(
        self,
        *,
        user_id: str,
        checkpoint: DurableCheckpointContext | None,
        used_tokens: int,
        selections: list[ActiveMemorySelectionRecord],
    ) -> DurableCheckpointContext | None:
        if checkpoint is None:
            return None
        selected = used_tokens + checkpoint.estimated_tokens <= self.token_budget
        selections.append(
            ActiveMemorySelectionRecord(
                memory_id_hash=_hash_id(
                    f"{user_id}:working:{checkpoint.checkpoint_version}"
                ),
                memory_layer=ActiveMemoryLayer.WORKING,
                memory_type="thread_checkpoint",
                source_type="durable_checkpoint",
                confidence="application_state",
                selected=selected,
                drop_reason=(
                    None if selected else ActiveMemoryDropReason.TOKEN_BUDGET
                ),
                estimated_tokens=checkpoint.estimated_tokens if selected else 0,
            )
        )
        return checkpoint if selected else None

    def _select_episodic(
        self,
        *,
        user_id: str,
        skill_name: str,
        current_request: str,
        result: MemoryRetrievalResult | None,
        retrieval_user_id: str | None,
        consent_allowed: bool,
        used_tokens: int,
        selections: list[ActiveMemorySelectionRecord],
    ) -> tuple[list[str], int]:
        if result is None:
            return [], 0
        allowed_types = _SKILL_EPISODIC_ALLOWLIST.get(skill_name, frozenset())
        rendered: list[str] = []
        episodic_tokens = 0
        for hit in result.hits:
            reason = self._episodic_drop_reason(
                user_id=user_id,
                hit=hit,
                allowed_types=allowed_types,
                current_request=current_request,
                retrieval_user_id=retrieval_user_id,
                consent_allowed=consent_allowed
                and result.diagnostics.consent_allowed,
                selected_count=len(rendered),
                used_tokens=used_tokens + episodic_tokens,
            )
            text = f"{hit.memory_type.value}: {hit.summary}"
            cost = self.token_estimator.count(text)
            if reason is None and used_tokens + episodic_tokens + cost > self.token_budget:
                reason = ActiveMemoryDropReason.TOKEN_BUDGET
            selected = reason is None
            if selected:
                rendered.append(text)
                episodic_tokens += cost
            selections.append(
                ActiveMemorySelectionRecord(
                    memory_id_hash=_hash_id(hit.memory_id),
                    memory_layer=ActiveMemoryLayer.EPISODIC,
                    memory_type=hit.memory_type.value,
                    source_type="episodic_memory",
                    confidence=f"{hit.score.confidence:.6f}",
                    retrieval_method=result.diagnostics.strategy.value,
                    retrieval_score=hit.score.total,
                    selected=selected,
                    drop_reason=reason,
                    estimated_tokens=cost if selected else 0,
                )
            )
        return rendered, episodic_tokens

    def _episodic_drop_reason(
        self,
        *,
        user_id: str,
        hit: MemoryRetrievalHit,
        allowed_types: frozenset[MemoryType],
        current_request: str,
        retrieval_user_id: str | None,
        consent_allowed: bool,
        selected_count: int,
        used_tokens: int,
    ) -> ActiveMemoryDropReason | None:
        del used_tokens
        if not consent_allowed:
            return ActiveMemoryDropReason.CONSENT_REQUIRED
        if retrieval_user_id != user_id:
            return ActiveMemoryDropReason.SCOPE_MISMATCH
        if hit.memory_type not in allowed_types:
            return ActiveMemoryDropReason.NOT_ALLOWED_FOR_SKILL
        if _conflicts_with_current(current_request, hit.summary):
            return ActiveMemoryDropReason.CURRENT_REQUEST_CONFLICT
        if selected_count >= self.max_episodic_items:
            return ActiveMemoryDropReason.MAX_ITEMS
        return None


def episodic_types_for_skill(skill_name: str) -> tuple[MemoryType, ...]:
    """Return the application-owned retrieval allowlist for one skill."""
    return tuple(
        sorted(
            _SKILL_EPISODIC_ALLOWLIST.get(skill_name, frozenset()),
            key=lambda item: item.value,
        )
    )


def _hash_id(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _conflicts_with_current(current: str, stored: str) -> bool:
    current_negative = any(marker in current.casefold() for marker in _NEGATION_MARKERS)
    stored_negative = any(marker in stored.casefold() for marker in _NEGATION_MARKERS)
    if current_negative == stored_negative:
        return False
    return len(lexical_terms(current).intersection(lexical_terms(stored))) >= 2
