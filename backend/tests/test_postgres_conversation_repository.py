"""Integration tests for PostgreSQL unified conversation persistence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest

from app.conversation.content_protector import LocalPlaintextContentProtector
from app.db.postgres.conversation_repository import (
    PostgresConversationRepository,
)
from app.models_conversation import (
    ConversationEventRole,
    ConversationEventType,
    ModuleProposal,
    ModuleProposalReason,
    ModuleProposalStatus,
    ModuleRun,
    ModuleType,
    RoleplayParameters,
)


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests.",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply all migrations to the configured integration database."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    command.upgrade(config, "head")


@pytest.fixture
def repository() -> PostgresConversationRepository:
    """Return a repository with an explicit non-production test protector."""
    assert TEST_DATABASE_URL is not None
    return PostgresConversationRepository(
        database_url=TEST_DATABASE_URL,
        protector=LocalPlaintextContentProtector(),
    )


def test_postgres_conversation_owner_scope_and_idempotency(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_conversation_user_{uuid4().hex}"
    conversation = repository.create(user_id=user_id, title="Integration")
    first = repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="hello",
        idempotency_key="message-1",
    )
    replay = repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="hello",
        idempotency_key="message-1",
    )

    assert replay.event_id == first.event_id
    assert repository.get_for_user(conversation.conversation_id, user_id)
    assert repository.get_for_user(conversation.conversation_id, "other") is None
    assert not repository.list_events(
        conversation_id=conversation.conversation_id,
        user_id="other",
    ).items

    domain_session_id = f"roleplay_{uuid4().hex}"
    repository.create_module_run(
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
    linked = repository.get_conversation_for_domain_session(
        user_id=user_id,
        module_type=ModuleType.ROLEPLAY,
        domain_session_id=domain_session_id,
    )
    assert linked is not None
    assert linked.conversation_id == conversation.conversation_id
    assert (
        repository.get_conversation_for_domain_session(
            user_id="other",
            module_type=ModuleType.ROLEPLAY,
            domain_session_id=domain_session_id,
        )
        is None
    )


def test_postgres_concurrent_append_sequence_is_contiguous(
    repository: PostgresConversationRepository,
) -> None:
    user_id = f"pg_conversation_concurrent_{uuid4().hex}"
    conversation = repository.create(user_id=user_id, title="Concurrent")

    def append(index: int) -> int:
        event = repository.append_event(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content=f"message {index}",
            idempotency_key=f"message-{index}",
        )
        return event.sequence_no

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append, range(16)))

    assert sorted(sequences) == list(range(1, 17))


def test_postgres_proposal_is_owner_scoped_and_deduplicated(
    repository: PostgresConversationRepository,
) -> None:
    """Exercise proposal persistence through PostgreSQL's typed bind path."""
    user_id = f"pg_proposal_user_{uuid4().hex}"
    conversation = repository.create(user_id=user_id, title="Proposal")
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

    assert repository.save_proposal(proposal) == proposal
    replay = repository.save_proposal(
        proposal.model_copy(update={"proposal_id": uuid4().hex})
    )
    assert replay.proposal_id == proposal.proposal_id
    accepted = repository.transition_proposal(
        proposal_id=proposal.proposal_id,
        conversation_id=conversation.conversation_id,
        user_id=user_id,
        expected_status=ModuleProposalStatus.PENDING,
        target_status=ModuleProposalStatus.ACCEPTED,
    )
    assert accepted is not None
    assert accepted.status is ModuleProposalStatus.ACCEPTED
    assert (
        repository.get_proposal_for_user(
            proposal_id=proposal.proposal_id,
            conversation_id=conversation.conversation_id,
            user_id="other",
        )
        is None
    )
