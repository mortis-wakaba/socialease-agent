"""PostgreSQL integration tests for durable memory repository parity."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

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
from app.memory.retriever import EpisodicMemoryRetriever
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
    MemoryProposalStatus,
    MemoryPolicyReason,
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
    PracticeThreadCheckpoint,
    PracticeThreadStatus,
    PendingMemoryProposalRecord,
)
from app.models_memory import UserConsentState
from app.models_memory_doctor import MemoryDoctorIssueCode
from app.models_scenario import ScenarioSpec
from app.services.scenario_interpreter import ScenarioInterpreter
from app.services.memory_doctor_service import MemoryDoctorService
from app.services.memory_privacy_service import MemoryPrivacyService


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")
NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests.",
)


def _scenario(description: str) -> ScenarioSpec:
    return ScenarioInterpreter().interpret(description=description).model_copy(
        update={"scenario_id": f"scenario_{description}"}
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


@pytest.mark.anyio
async def test_memory_fts_index_covers_the_custom_search_vector(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    """The PostgreSQL schema should expose a GIN custom-vector index."""
    async with repository.engine.connect() as connection:
        definition = (await connection.execute(
            text(
                """SELECT pg_get_indexdef(indexrelid)
                FROM pg_index
                WHERE indexrelid =
                    'idx_episodic_memories_search_vector'::regclass"""
            )
        )).scalar_one()

    normalized = " ".join(definition.casefold().split())
    assert "using gin" in normalized
    assert "socialease_memory_fts_text(summary)" in normalized


@pytest.mark.anyio
async def test_postgres_episodic_lifecycle_and_audit_are_atomic(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    user_id = f"pg_episodic_{uuid4().hex}"
    record = _memory(user_id)

    await repository.create_memory(record, reason_code="completed_practice")
    archived = await repository.transition_memory(
        memory_id=record.memory_id,
        user_id=user_id,
        expected_version=1,
        target_status=MemoryRecordStatus.ARCHIVED,
        reason_code="user_archived",
    )
    with pytest.raises(MemoryConflictError):
        await repository.transition_memory(
            memory_id=record.memory_id,
            user_id=user_id,
            expected_version=1,
            target_status=MemoryRecordStatus.ACTIVE,
            reason_code="stale_writer",
        )

    assert archived.version == 2
    assert await repository.get_memory(record.memory_id, f"other_{user_id}") is None
    events = await repository.list_events(user_id=user_id, subject_id=record.memory_id)
    assert [event.event_type for event in events] == [
        MemoryEventType.MEMORY_COMMITTED,
        MemoryEventType.MEMORY_ARCHIVED,
    ]


@pytest.mark.anyio
async def test_postgres_memory_center_update_and_checkpoint_listing(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    user_id = f"pg_memory_center_{uuid4().hex}"
    record = _memory(user_id)
    await repository.create_memory(record, reason_code="user_confirmed_proposal")
    updated = await repository.update_memory_summary(
        memory_id=record.memory_id,
        user_id=user_id,
        expected_version=1,
        summary="先写一个关键词，再开始表达。",
        content_hash="b" * 64,
        idempotency_key=uuid4().hex * 2,
        reason_code="user_edited",
    )
    checkpoint = _checkpoint(user_id, f"thread_{uuid4().hex}")
    await repository.save_checkpoint(
        checkpoint,
        expected_version=None,
        reason_code="practice_paused",
    )

    assert updated.version == 2
    assert updated.summary == "先写一个关键词，再开始表达。"
    assert await repository.list_checkpoints(user_id) == [checkpoint]
    events = await repository.list_events(
        user_id=user_id,
        subject_id=record.memory_id,
    )
    assert events[-1].event_type == MemoryEventType.MEMORY_UPDATED
    assert all(event.summary is None for event in events)


@pytest.mark.anyio
async def test_postgres_proposal_decision_erases_pending_body() -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_proposal_decision_{uuid4().hex}"
    repository = PostgresMemoryProposalRepository(
        database_url=TEST_DATABASE_URL
    )
    proposal = PendingMemoryProposalRecord(
        proposal_id=f"proposal_{uuid4().hex}",
        user_id=user_id,
        memory_type=MemoryType.SOCIAL_CONTEXT,
        summary="我更常在课程结束后参加小组讨论。",
        scenario_type="group_discussion",
        source_type=MemorySourceType.CHAT,
        source_id="pg-memory-center",
        evidence_type=MemoryEvidenceType.EXPLICIT_USER_STATEMENT,
        confidence=0.9,
        occurred_at=NOW,
        status=MemoryProposalStatus.PENDING_CONFIRMATION,
        policy_reason=MemoryPolicyReason.SOCIAL_CONTEXT_CONFIRMATION_REQUIRED,
        content_hash="c" * 64,
        idempotency_key=uuid4().hex * 2,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    await repository.save_pending(proposal)

    await repository.consume_pending(
        user_id=user_id,
        proposal_id=proposal.proposal_id,
        expected_version=1,
        target_status=MemoryProposalStatus.REJECTED,
        reason_code="user_rejected",
        changed_at=NOW + timedelta(minutes=1),
    )

    assert await repository.get_for_user(proposal.proposal_id, user_id) is None


@pytest.mark.anyio
async def test_postgres_checkpoint_export_and_user_delete_are_complete(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_checkpoint_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    checkpoint = _checkpoint(user_id, thread_id)
    await repository.save_checkpoint(
        checkpoint,
        expected_version=None,
        reason_code="practice_paused",
    )
    updated = await repository.save_checkpoint(
        checkpoint.model_copy(update={"status": PracticeThreadStatus.ACTIVE}),
        expected_version=1,
        reason_code="practice_resumed",
    )
    service = MemoryPrivacyService(database_url=TEST_DATABASE_URL)

    exported = await service.export(user_id)
    deleted = await service.delete(user_id)

    assert updated.version == 2
    assert len(exported.records["thread_checkpoints"]) == 1
    assert len(exported.records["memory_events"]) == 2
    assert deleted.deleted_counts["thread_checkpoints"] == 1
    assert deleted.deleted_counts["memory_events"] == 2
    assert await repository.get_checkpoint(thread_id, user_id) is None


@pytest.mark.anyio
async def test_postgres_checkpoint_service_restores_bounded_active_state(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_checkpoint_service_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    settings = PostgresUserMemorySettingsRepository(
        database_url=TEST_DATABASE_URL
    )
    await settings.save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    service = ThreadCheckpointService(
        repository=repository,
        settings_repository=settings,
        token_estimator=ConservativeTokenEstimator(),
        active_memory_token_budget=128,
    )

    paused = await service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=_scenario("classroom_speech"),
        current_stage="paused",
        status=PracticeThreadStatus.PAUSED,
        reason_code="practice_paused",
        helpful_strategy_codes=["short_sentence_first"],
        unresolved_next_step="恢复后先写一句简短开场。",
        changed_at=NOW,
    )
    restored = await service.restore_roleplay_context(
        user_id=user_id,
        thread_id=thread_id,
        expected_scenario_id=_scenario("classroom_speech").scenario_id,
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
    await settings.save(
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
    assert len(await repository.list_memories(user_id)) == 1


@pytest.mark.anyio
async def test_postgres_retrieval_is_user_scoped_and_audited(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    """PostgreSQL should enforce retrieval filters and atomically audit hits."""
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_retrieval_{uuid4().hex}"
    other_user_id = f"pg_retrieval_other_{uuid4().hex}"
    settings = PostgresUserMemorySettingsRepository(
        database_url=TEST_DATABASE_URL
    )
    await settings.save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )
    relevant = _memory(user_id).model_copy(
        update={
            "memory_type": MemoryType.HELPFUL_STRATEGY,
            "summary": "课堂发言前先准备一句简短开场有帮助。",
            "content_hash": uuid4().hex * 2,
            "idempotency_key": uuid4().hex * 2,
        }
    )
    other_user = _memory(other_user_id).model_copy(
        update={
            "memory_type": MemoryType.HELPFUL_STRATEGY,
            "summary": "课堂发言前先准备一句简短开场有帮助。",
            "content_hash": uuid4().hex * 2,
            "idempotency_key": uuid4().hex * 2,
        }
    )
    await repository.create_memory(relevant, reason_code="test_retrieval")
    await repository.create_memory(other_user, reason_code="test_retrieval")
    fts_candidates = await repository.search_memory_fts_candidates(
        user_id=user_id,
        statuses=(MemoryRecordStatus.ACTIVE,),
        memory_types=(MemoryType.HELPFUL_STRATEGY,),
        query_terms=("课堂", "发言", "开场"),
        now=NOW + timedelta(days=1),
        limit=10,
    )
    assert [item.memory_id for item in fts_candidates] == [relevant.memory_id]

    retriever = EpisodicMemoryRetriever(
        repository=repository,
        settings_repository=settings,
        context_token_budget=128,
    )

    result = await retriever.retrieve(
        MemoryRetrievalRequest(
            user_id=user_id,
            query="我想练习课堂发言的简短开场。",
            allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
            scenario_type="classroom_speech",
            strategy=MemoryRetrievalStrategy.SQL_TEXT,
        ),
        now=NOW + timedelta(days=1),
    )

    assert [hit.memory_id for hit in result.hits] == [relevant.memory_id]
    refreshed = await repository.get_memory(relevant.memory_id, user_id)
    assert refreshed is not None
    assert refreshed.last_retrieved_at == NOW + timedelta(days=1)
    events = await repository.list_events(
        user_id=user_id,
        subject_id=relevant.memory_id,
    )
    assert events[-1].event_type == MemoryEventType.MEMORY_RETRIEVED


@pytest.mark.anyio
async def test_postgres_memory_doctor_is_content_free_and_user_scoped(
    repository: PostgresLongTermMemoryRepository,
) -> None:
    """Doctor composes real PostgreSQL adapters without widening tenant scope."""
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_doctor_{uuid4().hex}"
    foreign_user_id = f"pg_doctor_foreign_{uuid4().hex}"
    first = _memory(user_id).model_copy(
        update={"summary": "先写一句开场有帮助。"}
    )
    duplicate = first.model_copy(
        update={
            "memory_id": f"memory_{uuid4().hex}",
            "idempotency_key": uuid4().hex * 2,
        }
    )
    foreign = _memory(foreign_user_id).model_copy(
        update={"summary": "foreign private memory body"}
    )
    await repository.create_memory(first, reason_code="doctor_test")
    await repository.create_memory(duplicate, reason_code="doctor_test")
    await repository.create_memory(foreign, reason_code="doctor_test")
    service = MemoryDoctorService(
        memory_repository=repository,
        proposal_repository=PostgresMemoryProposalRepository(
            database_url=TEST_DATABASE_URL
        ),
        settings_repository=PostgresUserMemorySettingsRepository(
            database_url=TEST_DATABASE_URL
        ),
    )

    report = await service.diagnose(user_id, now=NOW + timedelta(days=1))

    assert MemoryDoctorIssueCode.DUPLICATE_MEMORY in {
        issue.code for issue in report.issues
    }
    serialized = report.model_dump_json()
    assert first.summary not in serialized
    assert foreign.summary not in serialized
    assert foreign.memory_id not in serialized


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
