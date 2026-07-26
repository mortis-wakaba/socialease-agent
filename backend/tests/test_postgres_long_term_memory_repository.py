"""PostgreSQL integration tests for durable memory repository parity."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.db.postgres.long_term_memory_repository import (
    PostgresLongTermMemoryRepository,
)
from app.db.postgres.memory_proposal_repository import (
    PostgresMemoryProposalRepository,
)
from app.db.postgres.memory_settings_repository import (
    PostgresUserMemorySettingsRepository,
)
from app.memory.long_term_repository import MemoryConflictError
from app.memory.policy_engine import MemoryPolicyEngine
from app.memory.thread_checkpoint_service import ThreadCheckpointService
from app.memory.token_estimator import ConservativeTokenEstimator
from app.memory.write_pipeline import MemoryWritePipeline
from app.models import RiskLevel
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryExtractionResponse,
    MemoryEvidenceType,
    MemoryEventType,
    MemoryRecordStatus,
    MemorySourceType,
    MemoryType,
    MemoryProposal,
    PracticeThreadCheckpoint,
    PracticeThreadStatus,
)
from app.models_memory import UserConsentState
from app.models_roleplay import RoleplayScenario
from app.services.memory_privacy_service import MemoryPrivacyService


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")
NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests.",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply the complete Alembic chain before repository tests."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    command.upgrade(config, "head")


@pytest.fixture
def repository() -> PostgresLongTermMemoryRepository:
    """Return a PostgreSQL durable memory adapter."""
    assert TEST_DATABASE_URL is not None
    return PostgresLongTermMemoryRepository(database_url=TEST_DATABASE_URL)


def test_postgres_episodic_lifecycle_and_audit_are_atomic(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    user_id = f"pg_episodic_{uuid4().hex}"
    record = _memory(user_id)

    repository.create_memory(record, reason_code="completed_practice")
    archived = repository.transition_memory(
        memory_id=record.memory_id,
        user_id=user_id,
        expected_version=1,
        target_status=MemoryRecordStatus.ARCHIVED,
        reason_code="user_archived",
    )
    with pytest.raises(MemoryConflictError):
        repository.transition_memory(
            memory_id=record.memory_id,
            user_id=user_id,
            expected_version=1,
            target_status=MemoryRecordStatus.ACTIVE,
            reason_code="stale_writer",
        )

    assert archived.version == 2
    assert repository.get_memory(record.memory_id, f"other_{user_id}") is None
    events = repository.list_events(user_id=user_id, subject_id=record.memory_id)
    assert [event.event_type for event in events] == [
        MemoryEventType.MEMORY_COMMITTED,
        MemoryEventType.MEMORY_ARCHIVED,
    ]


def test_postgres_checkpoint_export_and_user_delete_are_complete(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_checkpoint_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    checkpoint = _checkpoint(user_id, thread_id)
    repository.save_checkpoint(
        checkpoint,
        expected_version=None,
        reason_code="practice_paused",
    )
    updated = repository.save_checkpoint(
        checkpoint.model_copy(update={"status": PracticeThreadStatus.ACTIVE}),
        expected_version=1,
        reason_code="practice_resumed",
    )
    service = MemoryPrivacyService(database_url=TEST_DATABASE_URL)

    exported = service.export(user_id)
    deleted = service.delete(user_id)

    assert updated.version == 2
    assert len(exported.records["thread_checkpoints"]) == 1
    assert len(exported.records["memory_events"]) == 2
    assert deleted.deleted_counts["thread_checkpoints"] == 1
    assert deleted.deleted_counts["memory_events"] == 2
    assert repository.get_checkpoint(thread_id, user_id) is None


def test_postgres_checkpoint_service_restores_bounded_active_state(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_checkpoint_service_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    settings = PostgresUserMemorySettingsRepository(
        database_url=TEST_DATABASE_URL
    )
    settings.save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    service = ThreadCheckpointService(
        repository=repository,
        settings_repository=settings,
        token_estimator=ConservativeTokenEstimator(),
        active_memory_token_budget=128,
    )

    paused = service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=RoleplayScenario.CLASSROOM_SPEECH,
        current_stage="paused",
        status=PracticeThreadStatus.PAUSED,
        reason_code="practice_paused",
        helpful_strategy_codes=["short_sentence_first"],
        unresolved_next_step="恢复后先写一句简短开场。",
        changed_at=NOW,
    )
    restored = service.restore_roleplay_context(
        user_id=user_id,
        thread_id=thread_id,
        expected_scenario=RoleplayScenario.CLASSROOM_SPEECH,
        now=NOW + timedelta(days=1),
    )

    assert paused is not None
    assert restored is not None
    assert restored.estimated_tokens <= restored.token_budget == 128
    assert restored.checkpoint_version == paused.version


@pytest.mark.anyio
async def test_postgres_policy_pipeline_commits_and_deduplicates(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_pipeline_{uuid4().hex}"
    settings = PostgresUserMemorySettingsRepository(
        database_url=TEST_DATABASE_URL
    )
    settings.save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    proposal = MemoryProposal(
        memory_type=MemoryType.HELPFUL_STRATEGY,
        summary="先写一句简短开场有助于课堂表达练习。",
        scenario_type="classroom_speech",
        source_type=MemorySourceType.CHAT,
        source_id="request_pg_1",
        evidence_type=MemoryEvidenceType.EXPLICIT_USER_STATEMENT,
        confidence=0.95,
        occurred_at=NOW,
    )
    pipeline = MemoryWritePipeline(
        extractor=_StaticExtractor(proposal),
        policy_engine=MemoryPolicyEngine(),
        memory_repository=repository,
        proposal_repository=PostgresMemoryProposalRepository(
            database_url=TEST_DATABASE_URL
        ),
        settings_repository=settings,
    )

    first = await pipeline.process_messages(
        user_id=user_id,
        messages=[{"role": "user", "content": "先写开场有帮助。"}],
        source_type=MemorySourceType.CHAT,
        source_id="request_pg_1",
        occurred_at=NOW,
        risk_level=RiskLevel.LOW,
        now=NOW,
    )
    second = await pipeline.process_messages(
        user_id=user_id,
        messages=[{"role": "user", "content": "先写开场有帮助。"}],
        source_type=MemorySourceType.CHAT,
        source_id="request_pg_1",
        occurred_at=NOW,
        risk_level=RiskLevel.LOW,
        now=NOW,
    )

    assert first.status == "committed"
    assert second.items[0].deduplicated is True
    assert len(repository.list_memories(user_id)) == 1


def _memory(user_id: str) -> EpisodicMemoryRecord:
    return EpisodicMemoryRecord(
        memory_id=f"memory_{uuid4().hex}",
        user_id=user_id,
        memory_type=MemoryType.PRACTICE_EXPERIENCE,
        summary="完成了一次低强度课堂表达练习。",
        scenario_type="classroom_speech",
        source_type=MemorySourceType.SESSION_REVIEW,
        source_id=f"review_{uuid4().hex}",
        evidence_type=MemoryEvidenceType.COMPLETED_PRODUCT_ACTION,
        confidence=1.0,
        occurred_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=180),
        consent_version="practice-summary-v1",
        content_hash="b" * 64,
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
        unresolved_next_step="先写一句简短开场。",
        status=PracticeThreadStatus.PAUSED,
        last_activity_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


class _StaticExtractor:
    """Return one validated proposal without invoking a provider."""

    enabled = True

    def __init__(self, proposal: MemoryProposal) -> None:
        self.proposal = proposal

    async def extract(self, **kwargs: object) -> MemoryExtractionResponse:
        del kwargs
        return MemoryExtractionResponse(proposals=[self.proposal])
