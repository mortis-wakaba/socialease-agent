"""Integration tests for PostgreSQL unified conversation persistence."""

import asyncio
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.conversation.content_protector import LocalPlaintextContentProtector
from app.conversation.repository import ConversationConcurrencyError
from app.db.postgres.conversation_repository import (
    PostgresConversationRepository,
)
from app.db.postgres.worksheet_repository import PostgresWorksheetRepository
from app.models_conversation import (
    ConversationEventRole,
    ConversationEventType,
    ModuleProposal,
    ModuleProposalReason,
    ModuleProposalStatus,
    ModuleRun,
    ModuleType,
    RoleplayParameters,
    WorksheetParameters,
)
from app.models_worksheet import WORKSHEET_DISCLAIMER, WorksheetFields, WorksheetRecord


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.usefixtures("migrated_database"),
    pytest.mark.anyio,
]


@pytest.fixture
def repository() -> PostgresConversationRepository:
    """Return a repository with an explicit non-production test protector."""
    assert TEST_DATABASE_URL is not None
    return PostgresConversationRepository(
        database_url=TEST_DATABASE_URL,
        protector=LocalPlaintextContentProtector(),
    )


async def test_postgres_conversation_owner_scope_and_idempotency(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_conversation_user_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Integration")
    first = await repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="hello",
        idempotency_key="message-1",
    )
    replay = await repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="hello",
        idempotency_key="message-1",
    )

    assert replay.event_id == first.event_id
    assert await repository.get_for_user(conversation.conversation_id, user_id)
    assert await repository.get_for_user(conversation.conversation_id, "other") is None
    assert not (
        await repository.list_events(
            conversation_id=conversation.conversation_id,
            user_id="other",
        )
    ).items

    domain_session_id = f"roleplay_{uuid4().hex}"
    await repository.create_module_run(
        ModuleRun(
            module_run_id=f"module_{uuid4().hex}",
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            module_type=ModuleType.ROLEPLAY,
            depth=1,
            module_parameters=RoleplayParameters(
                scenario_description="Integration role-play",
            ),
            domain_session_id=domain_session_id,
            started_at=conversation.created_at,
        )
    )
    linked = await repository.get_conversation_for_domain_session(
        user_id=user_id,
        module_type=ModuleType.ROLEPLAY,
        domain_session_id=domain_session_id,
    )
    assert linked is not None
    assert linked.conversation_id == conversation.conversation_id
    assert (
        await repository.get_conversation_for_domain_session(
            user_id="other",
            module_type=ModuleType.ROLEPLAY,
            domain_session_id=domain_session_id,
        )
        is None
    )


async def test_expired_conversation_command_lease_is_reclaimed(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_command_lease_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Lease")
    values = {
        "conversation_id": conversation.conversation_id,
        "user_id": user_id,
        "idempotency_key": "leased-command-001",
        "request_hash": "a" * 64,
    }

    first = await repository.claim_command(
        **values,
        lease_owner="worker-one",
        lease_seconds=60,
    )
    async with repository.engine.begin() as connection:
        await connection.execute(
            text(
                """UPDATE conversation_commands
                SET lease_expires_at = :expired
                WHERE conversation_id = :conversation_id
                  AND idempotency_key = :idempotency_key"""
            ),
            {
                "expired": datetime.now(UTC) - timedelta(seconds=1),
                "conversation_id": conversation.conversation_id,
                "idempotency_key": values["idempotency_key"],
            },
        )
    reclaimed = await repository.claim_command(
        **values,
        lease_owner="worker-two",
        lease_seconds=60,
    )

    assert first.acquired is True
    assert first.attempt_count == 1
    assert reclaimed.acquired is True
    assert reclaimed.attempt_count == 2
    with pytest.raises(ConversationConcurrencyError):
        await repository.complete_command(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            idempotency_key=values["idempotency_key"],
            lease_owner="worker-one",
            result='{"response":"stale"}',
        )
    await repository.complete_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=values["idempotency_key"],
        lease_owner="worker-two",
        result='{"response":"done"}',
    )
    replay = await repository.claim_command(
        **values,
        lease_owner="worker-three",
        lease_seconds=60,
    )
    assert replay.completed_result == '{"response":"done"}'


async def test_different_commands_are_serialized_per_conversation(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_command_serial_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Serial")
    first, second = await asyncio.gather(
        repository.claim_command(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            idempotency_key="first-command",
            request_hash="a" * 64,
            lease_owner="worker-one",
            lease_seconds=60,
        ),
        repository.claim_command(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            idempotency_key="second-command",
            request_hash="b" * 64,
            lease_owner="worker-two",
            lease_seconds=60,
        ),
    )

    assert sum(claim.acquired for claim in (first, second)) == 1


async def test_expired_command_can_be_abandoned_before_new_work(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_command_abandon_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Recovery")
    first_key = "abandon-command-001"
    await repository.claim_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=first_key,
        request_hash="d" * 64,
        lease_owner="stale-worker",
        lease_seconds=60,
    )
    await repository.record_command_safety_classification(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=first_key,
        lease_owner="stale-worker",
        crisis=False,
    )
    async with repository.engine.begin() as connection:
        await connection.execute(
            text(
                """UPDATE conversation_commands
                SET lease_expires_at = :expired
                WHERE conversation_id = :conversation_id
                  AND idempotency_key = :idempotency_key"""
            ),
            {
                "expired": datetime.now(UTC) - timedelta(seconds=1),
                "conversation_id": conversation.conversation_id,
                "idempotency_key": first_key,
            },
        )

    recoverable = await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=first_key,
    )
    assert recoverable is not None and recoverable.status == "recoverable"
    abandoned = await repository.abandon_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=first_key,
    )
    assert abandoned is not None and abandoned.status == "abandoned"

    next_claim = await repository.claim_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key="next-command-001",
        request_hash="e" * 64,
        lease_owner="next-worker",
        lease_seconds=60,
    )
    assert next_claim.acquired is True
    with repository.command_lease_scope(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=first_key,
        lease_owner="stale-worker",
    ):
        with pytest.raises(ConversationConcurrencyError, match="lease"):
            await repository.append_event(
                conversation_id=conversation.conversation_id,
                user_id=user_id,
                event_type=ConversationEventType.ASSISTANT_MESSAGE,
                role=ConversationEventRole.ASSISTANT,
                content="stale write",
                idempotency_key="stale-abandoned-write",
            )


async def test_crisis_priority_claim_abandons_different_active_command(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_crisis_preempt_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Crisis priority")
    await repository.claim_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key="ordinary-command",
        request_hash="f" * 64,
        lease_owner="ordinary-worker",
        lease_seconds=60,
    )
    await repository.record_command_safety_classification(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key="ordinary-command",
        lease_owner="ordinary-worker",
        crisis=False,
    )

    crisis = await repository.claim_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key="crisis-command",
        request_hash="1" * 64,
        lease_owner="crisis-worker",
        lease_seconds=60,
        preempt_active=True,
    )

    assert crisis.acquired is True
    old = await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key="ordinary-command",
    )
    assert old is not None and old.status == "abandoned"
    assert await repository.get_command(
        conversation_id=conversation.conversation_id,
        user_id="other-user",
        idempotency_key="ordinary-command",
    ) is None


async def test_expired_lease_cannot_renew_or_write_after_takeover(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_command_fence_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Fence")
    values = {
        "conversation_id": conversation.conversation_id,
        "user_id": user_id,
        "idempotency_key": "fenced-command",
        "request_hash": "c" * 64,
    }
    await repository.claim_command(
        **values,
        lease_owner="stale-worker",
        lease_seconds=60,
    )
    async with repository.engine.begin() as connection:
        await connection.execute(
            text(
                """UPDATE conversation_commands
                SET lease_expires_at = :expired
                WHERE conversation_id = :conversation_id
                  AND idempotency_key = :idempotency_key"""
            ),
            {
                "expired": datetime.now(UTC) - timedelta(seconds=1),
                "conversation_id": conversation.conversation_id,
                "idempotency_key": values["idempotency_key"],
            },
        )
    assert not await repository.renew_command_lease(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=values["idempotency_key"],
        lease_owner="stale-worker",
        lease_seconds=60,
    )
    takeover = await repository.claim_command(
        **values,
        lease_owner="recovery-worker",
        lease_seconds=60,
    )
    assert takeover.acquired is True

    with repository.command_lease_scope(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        idempotency_key=values["idempotency_key"],
        lease_owner="stale-worker",
    ):
        with pytest.raises(ConversationConcurrencyError, match="lease"):
            await repository.append_event(
                conversation_id=conversation.conversation_id,
                user_id=user_id,
                event_type=ConversationEventType.ASSISTANT_MESSAGE,
                role=ConversationEventRole.ASSISTANT,
                content="stale side effect",
                idempotency_key="stale-side-effect",
            )


async def test_postgres_concurrent_append_sequence_is_contiguous(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_conversation_concurrent_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Concurrent")

    async def append(index: int) -> int:
        event = await repository.append_event(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content=f"message {index}",
            idempotency_key=f"message-{index}",
        )
        return event.sequence_no

    sequences = await asyncio.gather(*(append(index) for index in range(16)))

    assert sorted(sequences) == list(range(1, 17))


async def test_postgres_proposal_is_owner_scoped_and_deduplicated(
    repository: PostgresConversationRepository,
) -> None:
    """Exercise proposal persistence through PostgreSQL's typed bind path."""
    user_id = f"pg_proposal_user_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Proposal")
    now = datetime.now(UTC)
    proposal = ModuleProposal(
        proposal_id=uuid4().hex,
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        proposed_module=ModuleType.ROLEPLAY,
        reason_code=ModuleProposalReason.EXPLICIT_PRACTICE_REQUEST,
        bounded_parameters=RoleplayParameters(
            scenario_description="课堂发言练习",
        ),
        request_hash=uuid4().hex,
        expires_at=now + timedelta(minutes=10),
        created_at=now,
    )

    assert await repository.save_proposal(proposal) == proposal
    replay = await repository.save_proposal(
        proposal.model_copy(update={"proposal_id": uuid4().hex})
    )
    assert replay.proposal_id == proposal.proposal_id
    accepted = await repository.transition_proposal(
        proposal_id=proposal.proposal_id,
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        expected_status=ModuleProposalStatus.PENDING,
        target_status=ModuleProposalStatus.ACCEPTED,
    )
    assert accepted is not None
    assert accepted.status is ModuleProposalStatus.ACCEPTED
    assert (
        await repository.get_proposal_for_user(
            proposal_id=proposal.proposal_id,
            conversation_id=conversation.conversation_id,
            user_id="other",
        )
        is None
    )


async def test_module_and_domain_session_rollback_together(
    repository: PostgresConversationRepository,
) -> None:
    """Prove a domain write cannot survive a failed module-start transaction."""
    assert TEST_DATABASE_URL is not None
    user_id = f"pg_module_tx_{uuid4().hex}"
    conversation = await repository.create(user_id=user_id, title="Atomic")
    now = datetime.now(UTC)
    proposal = ModuleProposal(
        proposal_id=uuid4().hex,
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        proposed_module=ModuleType.WORKSHEET,
        reason_code=ModuleProposalReason.EXPLICIT_PRACTICE_REQUEST,
        bounded_parameters=WorksheetParameters(situation="课堂发言"),
        request_hash=uuid4().hex,
        expires_at=now + timedelta(minutes=10),
        created_at=now,
    )
    await repository.save_proposal(proposal)
    run = ModuleRun(
        module_run_id=uuid4().hex,
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        module_type=ModuleType.WORKSHEET,
        depth=1,
        module_parameters=proposal.bounded_parameters,
        domain_session_id=uuid4().hex,
        started_at=now,
    )
    worksheets = PostgresWorksheetRepository(
        engine=repository.engine,
    )
    record = WorksheetRecord(
        worksheet_id=run.domain_session_id or "",
        user_id=user_id,
        source_event_id=None,
        fields=WorksheetFields(situation="课堂发言"),
        citations=[],
        disclaimer=WORKSHEET_DISCLAIMER,
        missing_fields=["automatic_thought"],
        gentle_followup_questions=[],
        created_at=now,
        updated_at=now,
    )

    with pytest.raises(RuntimeError, match="fault injection"):
        async with repository.module_start_transaction():
            await repository.begin_module_start(
                proposal=proposal,
                run=run,
                parent=None,
            )
            await worksheets.save(record)
            raise RuntimeError("fault injection")

    stored_proposal = await repository.get_proposal_for_user(
        proposal_id=proposal.proposal_id,
        conversation_id=conversation.conversation_id,
        user_id=user_id,
    )
    assert stored_proposal is not None
    assert stored_proposal.status is ModuleProposalStatus.PENDING
    assert (
        await repository.get_module_run_for_user(
            module_run_id=run.module_run_id,
            conversation_id=conversation.conversation_id,
            user_id=user_id,
        )
        is None
    )
    assert await worksheets.get(record.worksheet_id) is None
