"""Contract tests for durable episodic memory and thread checkpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.memory.long_term_repository import (
    InvalidMemoryTransitionError,
    LongTermMemoryRepository,
    MemoryConflictError,
    MemoryNotFoundError,
)
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvidenceType,
    MemoryEventType,
    MemoryRecordStatus,
    MemorySourceType,
    MemoryType,
    PracticeThreadCheckpoint,
    PracticeThreadStatus,
)
from app.services.memory_privacy_service import MemoryPrivacyService


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


def _memory(user_id: str) -> EpisodicMemoryRecord:
    return EpisodicMemoryRecord(
        memory_id=f"memory_{uuid4().hex}",
        user_id=user_id,
        memory_type=MemoryType.PRACTICE_EXPERIENCE,
        summary="在小组讨论中完成了一次简短表达练习。",
        scenario_type="group_discussion",
        source_type=MemorySourceType.SESSION_REVIEW,
        source_id=f"review_{uuid4().hex}",
        evidence_type=MemoryEvidenceType.COMPLETED_PRODUCT_ACTION,
        confidence=1.0,
        status=MemoryRecordStatus.ACTIVE,
        occurred_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=180),
        consent_version="practice-summary-v1",
        content_hash="a" * 64,
        idempotency_key=uuid4().hex * 2,
    )


def _checkpoint(user_id: str, thread_id: str) -> PracticeThreadCheckpoint:
    return PracticeThreadCheckpoint(
        thread_id=thread_id,
        user_id=user_id,
        current_goal="clearer_classroom_expression",
        current_stage="prepare_opening",
        current_scenario="classroom_speech",
        helpful_strategy_codes=["short_sentence_first"],
        attempted_skill_names=["roleplay_skill"],
        unresolved_next_step="先写下一句开场。",
        status=PracticeThreadStatus.PAUSED,
        version=1,
        last_activity_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_episodic_memory_lifecycle_is_scoped_versioned_and_audited(
    long_term_memory_repository_contract: LongTermMemoryRepository,
) -> None:
    repository = long_term_memory_repository_contract
    user_id = f"episodic_owner_{uuid4().hex}"
    other_user_id = f"episodic_other_{uuid4().hex}"
    original = _memory(user_id)

    created = await repository.create_memory(
        original,
        reason_code="completed_practice",
    )
    archived = await repository.transition_memory(
        memory_id=original.memory_id,
        user_id=user_id,
        expected_version=1,
        target_status=MemoryRecordStatus.ARCHIVED,
        reason_code="user_archived",
        changed_at=NOW + timedelta(minutes=1),
    )
    restored = await repository.transition_memory(
        memory_id=original.memory_id,
        user_id=user_id,
        expected_version=2,
        target_status=MemoryRecordStatus.ACTIVE,
        reason_code="user_restored",
        changed_at=NOW + timedelta(minutes=2),
    )
    revoked = await repository.transition_memory(
        memory_id=original.memory_id,
        user_id=user_id,
        expected_version=3,
        target_status=MemoryRecordStatus.REVOKED,
        reason_code="consent_revoked",
        changed_at=NOW + timedelta(minutes=3),
    )

    assert created.version == 1
    assert archived.status == MemoryRecordStatus.ARCHIVED
    assert archived.version == 2
    assert restored.status == MemoryRecordStatus.ACTIVE
    assert restored.version == 3
    assert revoked.status == MemoryRecordStatus.REVOKED
    assert revoked.version == 4
    assert await repository.get_memory(original.memory_id, other_user_id) is None
    assert await repository.list_memories(other_user_id) == []
    assert await repository.list_memories(user_id) == [revoked]
    events = await repository.list_events(
        user_id=user_id,
        subject_id=original.memory_id,
    )
    assert [event.event_type for event in events] == [
        MemoryEventType.MEMORY_COMMITTED,
        MemoryEventType.MEMORY_ARCHIVED,
        MemoryEventType.MEMORY_REACTIVATED,
        MemoryEventType.MEMORY_REVOKED,
    ]
    assert all(event.summary is None for event in events)


@pytest.mark.anyio
async def test_memory_rejects_stale_version_invalid_transition_and_duplicate_id(
    long_term_memory_repository_contract: LongTermMemoryRepository,
) -> None:
    repository = long_term_memory_repository_contract
    user_id = f"episodic_conflict_{uuid4().hex}"
    record = _memory(user_id)
    await repository.create_memory(record, reason_code="completed_practice")

    with pytest.raises(MemoryConflictError):
        await repository.create_memory(record, reason_code="duplicate_retry")
    with pytest.raises(MemoryConflictError):
        await repository.transition_memory(
            memory_id=record.memory_id,
            user_id=user_id,
            expected_version=9,
            target_status=MemoryRecordStatus.ARCHIVED,
            reason_code="stale_writer",
        )

    await repository.transition_memory(
        memory_id=record.memory_id,
        user_id=user_id,
        expected_version=1,
        target_status=MemoryRecordStatus.REVOKED,
        reason_code="consent_revoked",
    )
    with pytest.raises(InvalidMemoryTransitionError):
        await repository.transition_memory(
            memory_id=record.memory_id,
            user_id=user_id,
            expected_version=2,
            target_status=MemoryRecordStatus.ACTIVE,
            reason_code="invalid_restore",
        )
    with pytest.raises(MemoryNotFoundError):
        await repository.transition_memory(
            memory_id=record.memory_id,
            user_id=f"wrong_{user_id}",
            expected_version=2,
            target_status=MemoryRecordStatus.ARCHIVED,
            reason_code="cross_user_attempt",
        )


@pytest.mark.anyio
async def test_memory_delete_physically_removes_body_but_keeps_content_free_event(
    long_term_memory_repository_contract: LongTermMemoryRepository,
) -> None:
    repository = long_term_memory_repository_contract
    user_id = f"episodic_delete_{uuid4().hex}"
    record = _memory(user_id)
    await repository.create_memory(record, reason_code="completed_practice")

    await repository.delete_memory(
        memory_id=record.memory_id,
        user_id=user_id,
        expected_version=1,
        reason_code="user_deleted",
    )

    assert await repository.get_memory(record.memory_id, user_id) is None
    events = await repository.list_events(
        user_id=user_id, subject_id=record.memory_id
    )
    assert events[-1].event_type == MemoryEventType.MEMORY_DELETED
    assert record.summary not in str([event.model_dump() for event in events])


@pytest.mark.anyio
async def test_checkpoint_compare_and_swap_prevents_silent_overwrite(
    long_term_memory_repository_contract: LongTermMemoryRepository,
) -> None:
    repository = long_term_memory_repository_contract
    user_id = f"checkpoint_owner_{uuid4().hex}"
    other_user_id = f"checkpoint_other_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    checkpoint = _checkpoint(user_id, thread_id)

    created = await repository.save_checkpoint(
        checkpoint,
        expected_version=None,
        reason_code="practice_paused",
    )
    updated = await repository.save_checkpoint(
        checkpoint.model_copy(
            update={
                "unresolved_next_step": "尝试说出一句简短观点。",
                "status": PracticeThreadStatus.ACTIVE,
            }
        ),
        expected_version=1,
        reason_code="practice_resumed",
        changed_at=NOW + timedelta(minutes=1),
    )

    assert created.version == 1
    assert updated.version == 2
    assert updated.unresolved_next_step == "尝试说出一句简短观点。"
    assert await repository.get_checkpoint(thread_id, user_id) == updated
    assert await repository.get_checkpoint(thread_id, other_user_id) is None
    with pytest.raises(MemoryConflictError):
        await repository.save_checkpoint(
            checkpoint,
            expected_version=1,
            reason_code="stale_checkpoint_writer",
        )
    events = await repository.list_events(user_id=user_id, subject_id=thread_id)
    assert [event.event_type for event in events] == [
        MemoryEventType.CHECKPOINT_UPDATED,
        MemoryEventType.CHECKPOINT_UPDATED,
    ]
    assert [event.subject_version for event in events] == [1, 2]


@pytest.mark.anyio
async def test_user_export_and_delete_cover_all_long_term_memory_tables(
    long_term_memory_repository_contract: LongTermMemoryRepository,
) -> None:
    repository = long_term_memory_repository_contract
    user_id = f"long_term_export_{uuid4().hex}"
    record = _memory(user_id)
    checkpoint = _checkpoint(user_id, f"thread_{uuid4().hex}")
    await repository.create_memory(record, reason_code="completed_practice")
    await repository.save_checkpoint(
        checkpoint,
        expected_version=None,
        reason_code="practice_paused",
    )
    service = MemoryPrivacyService()

    exported = await service.export(user_id)
    deleted = await service.delete(user_id)
    exported_after_delete = await service.export(user_id)

    assert len(exported.records["episodic_memories"]) == 1
    assert len(exported.records["thread_checkpoints"]) == 1
    assert len(exported.records["memory_events"]) == 2
    assert deleted.deleted_counts["episodic_memories"] == 1
    assert deleted.deleted_counts["thread_checkpoints"] == 1
    assert deleted.deleted_counts["memory_events"] == 2
    assert all(not rows for rows in exported_after_delete.records.values())
