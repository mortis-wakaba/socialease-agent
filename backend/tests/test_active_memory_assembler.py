"""Tests for deterministic, scope-safe active memory assembly."""

from datetime import datetime, timezone
import json

from app.memory.active_memory_assembler import ActiveMemoryAssembler
from app.models_active_memory import ActiveMemoryDropReason, ActiveMemoryLayer
from app.models_context import (
    ContextConfidence,
    ContextFieldMetadata,
    ContextValueSource,
    SkillContextProjection,
)
from app.models_long_term_memory import (
    MemoryRecordStatus,
    MemoryRetrievalDiagnostics,
    MemoryRetrievalHit,
    MemoryRetrievalResult,
    MemoryRetrievalScore,
    MemoryRetrievalStrategy,
    MemoryType,
)
from app.models_session_context import DurableCheckpointContext, RoleplayCompactState


NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


class CharacterEstimator:
    """Predictable estimator used to make budget assertions exact."""

    backend_name = "test_characters"
    model_name = None

    def count(self, text: str) -> int:
        return max(1, len(text))


def _projection(skill_name: str = "roleplay_skill") -> SkillContextProjection:
    return SkillContextProjection(
        skill_name=skill_name,
        values={"scenario": "group_discussion"},
        selected_fields=["scenario"],
        field_metadata={
            "scenario": ContextFieldMetadata(
                sources=[ContextValueSource.CURRENT_REQUEST],
                confidence=ContextConfidence.EXPLICIT,
            )
        },
        selected_at=NOW,
    )


def _hit(
    memory_id: str,
    memory_type: MemoryType,
    summary: str,
    *,
    score: float = 0.8,
) -> MemoryRetrievalHit:
    return MemoryRetrievalHit(
        memory_id=memory_id,
        memory_type=memory_type,
        summary=summary,
        status=MemoryRecordStatus.ACTIVE,
        occurred_at=NOW,
        score=MemoryRetrievalScore(
            lexical=0.7,
            scenario=1.0,
            recency=1.0,
            novelty=1.0,
            confidence=0.9,
            total=score,
        ),
        estimated_tokens=20,
    )


def _retrieval(*hits: MemoryRetrievalHit, consent: bool = True) -> MemoryRetrievalResult:
    return MemoryRetrievalResult(
        hits=list(hits),
        diagnostics=MemoryRetrievalDiagnostics(
            strategy=MemoryRetrievalStrategy.SQL_TEXT,
            candidate_count=len(hits),
            eligible_count=len(hits),
            returned_count=len(hits),
            estimated_tokens=sum(hit.estimated_tokens for hit in hits),
            token_budget=256,
            abstained=not hits,
            consent_allowed=consent,
        ),
    )


def _checkpoint(tokens: int = 40) -> DurableCheckpointContext:
    return DurableCheckpointContext(
        compact_state=RoleplayCompactState(
            current_topic="scenario:group_discussion",
            version=2,
            updated_at=NOW,
        ),
        checkpoint_version=2,
        estimated_tokens=tokens,
        token_budget=256,
    )


def test_roleplay_allowlist_selects_only_permitted_memory_types() -> None:
    assembler = ActiveMemoryAssembler(token_budget=512)
    packet = assembler.assemble(
        user_id="user-a",
        skill_context=_projection(),
        current_request="小组讨论时怎么表达观点",
        memory_retrieval=_retrieval(
            _hit("allowed", MemoryType.HELPFUL_STRATEGY, "先复述再表达观点有帮助"),
            _hit("blocked", MemoryType.SOCIAL_CONTEXT, "经常和某位同学一起讨论"),
        ),
        retrieval_user_id="user-a",
        assembled_at=NOW,
    )

    assert packet.episodic_memories == [
        "helpful_strategy: 先复述再表达观点有帮助"
    ]
    blocked = next(
        item for item in packet.selections if item.memory_type == "social_context"
    )
    assert blocked.drop_reason == ActiveMemoryDropReason.NOT_ALLOWED_FOR_SKILL


def test_general_support_receives_only_helpful_strategy_and_practice_experience() -> None:
    packet = ActiveMemoryAssembler().assemble(
        user_id="user-a",
        skill_context=_projection("general_support_skill"),
        current_request="今天有点紧张",
        memory_retrieval=_retrieval(
            _hit("memory-1", MemoryType.HELPFUL_STRATEGY, "先停顿一下有帮助"),
            _hit("memory-2", MemoryType.PRACTICE_EXPERIENCE, "完成过一次简短开场"),
            _hit("memory-3", MemoryType.PRACTICE_MILESTONE, "连续练习了三次"),
        ),
        retrieval_user_id="user-a",
        assembled_at=NOW,
    )

    assert packet.episodic_memories == [
        "helpful_strategy: 先停顿一下有帮助",
        "practice_experience: 完成过一次简短开场",
    ]
    assert packet.selections[-1].drop_reason == (
        ActiveMemoryDropReason.NOT_ALLOWED_FOR_SKILL
    )


def test_retrieval_scope_mismatch_fails_closed() -> None:
    packet = ActiveMemoryAssembler().assemble(
        user_id="user-a",
        skill_context=_projection(),
        current_request="讨论练习",
        memory_retrieval=_retrieval(
            _hit("foreign-memory", MemoryType.PRACTICE_EXPERIENCE, "讨论练习完成")
        ),
        retrieval_user_id="user-b",
        assembled_at=NOW,
    )

    assert packet.episodic_memories == []
    assert packet.selections[-1].drop_reason == ActiveMemoryDropReason.SCOPE_MISMATCH


def test_consent_and_current_request_conflict_are_rechecked() -> None:
    assembler = ActiveMemoryAssembler()
    no_consent = assembler.assemble(
        user_id="user-a",
        skill_context=_projection(),
        current_request="继续讨论",
        memory_retrieval=_retrieval(
            _hit("m1", MemoryType.HELPFUL_STRATEGY, "先复述观点有帮助"),
            consent=False,
        ),
        retrieval_user_id="user-a",
        assembled_at=NOW,
    )
    conflict = assembler.assemble(
        user_id="user-a",
        skill_context=_projection(),
        current_request="不要再复述观点，这个方法没用",
        memory_retrieval=_retrieval(
            _hit("m1", MemoryType.HELPFUL_STRATEGY, "先复述观点很有帮助")
        ),
        retrieval_user_id="user-a",
        assembled_at=NOW,
    )

    assert no_consent.selections[-1].drop_reason == (
        ActiveMemoryDropReason.CONSENT_REQUIRED
    )
    assert conflict.selections[-1].drop_reason == (
        ActiveMemoryDropReason.CURRENT_REQUEST_CONFLICT
    )


def test_one_budget_preserves_stable_then_working_before_episodic() -> None:
    assembler = ActiveMemoryAssembler(
        token_estimator=CharacterEstimator(),
        token_budget=128,
    )
    kwargs = {
        "user_id": "user-a",
        "skill_context": _projection(),
        "current_request": "小组讨论观点",
        "durable_checkpoint": _checkpoint(tokens=80),
        "memory_retrieval": _retrieval(
            _hit(
                "long",
                MemoryType.HELPFUL_STRATEGY,
                "先复述对方观点，再用一句简短的话表达自己的不同意见",
            )
        ),
        "retrieval_user_id": "user-a",
        "assembled_at": NOW,
    }

    first = assembler.assemble(**kwargs)
    second = assembler.assemble(**kwargs)

    assert first.stable_memory.selected_fields == ["scenario"]
    assert first.working_memory is not None
    assert first.episodic_memories == []
    assert first.estimated_tokens <= first.token_budget
    budget_drop = next(
        item
        for item in first.selections
        if item.drop_reason == ActiveMemoryDropReason.TOKEN_BUDGET
    )
    assert budget_drop.estimated_tokens > 0
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_trace_metadata_contains_hashes_but_no_memory_content_or_raw_ids() -> None:
    packet = ActiveMemoryAssembler().assemble(
        user_id="user-a",
        skill_context=_projection(),
        current_request="讨论观点",
        durable_checkpoint=_checkpoint(),
        memory_retrieval=_retrieval(
            _hit(
                "secret-memory-id",
                MemoryType.HELPFUL_STRATEGY,
                "先写下一个关键词再发言",
            )
        ),
        retrieval_user_id="user-a",
        assembled_at=NOW,
    )

    serialized = json.dumps(packet.trace_metadata(), ensure_ascii=False)
    assert "先写下一个关键词再发言" not in serialized
    assert "secret-memory-id" not in serialized
    assert any(
        item.memory_layer == ActiveMemoryLayer.EPISODIC
        and len(item.memory_id_hash) == 16
        for item in packet.selections
    )
