"""SQLite persistence tests for unified conversations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.conversation.content_protector import (
    AESGCMConversationContentProtector,
    ConversationContentProtectionError,
    ProtectedContent,
    configured_content_protector,
)
from app.conversation.repository import (
    ConversationConcurrencyError,
    ConversationIdempotencyError,
    SQLiteConversationRepository,
)
from app.models_conversation import (
    ConversationEventRole,
    ConversationEventType,
    ModuleProposal,
    ModuleProposalReason,
    ModuleProposalStatus,
    ModuleType,
    RoleplayParameters,
)


@pytest.fixture
def repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> SQLiteConversationRepository:
    """Return an isolated local repository."""
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "conversations.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    return SQLiteConversationRepository()


def test_create_list_get_and_owner_scope(
    repository: SQLiteConversationRepository,
) -> None:
    first = repository.create(user_id="owner", title="First")
    second = repository.create(user_id="owner", title="Second")
    repository.create(user_id="other", title="Other")

    assert repository.get_for_user(first.conversation_id, "owner") == first
    assert repository.get_for_user(first.conversation_id, "other") is None

    page = repository.list_for_user("owner", limit=1)
    assert [item.conversation_id for item in page.items] == [
        second.conversation_id
    ]
    assert page.next_cursor is not None
    next_page = repository.list_for_user(
        "owner",
        cursor=page.next_cursor,
        limit=1,
    )
    assert [item.conversation_id for item in next_page.items] == [
        first.conversation_id
    ]


def test_append_is_ordered_paginated_and_idempotent(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Timeline")
    first = repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="hello",
        idempotency_key="request-1",
    )
    replay = repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="hello",
        idempotency_key="request-1",
    )
    second = repository.append_event(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        event_type=ConversationEventType.ASSISTANT_MESSAGE,
        role=ConversationEventRole.ASSISTANT,
        content="hi",
        idempotency_key="response-1",
    )

    assert replay.event_id == first.event_id
    assert second.sequence_no == 2
    page = repository.list_events(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        limit=1,
    )
    assert [event.content for event in page.items] == ["hello"]
    assert page.next_cursor
    next_page = repository.list_events(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        cursor=page.next_cursor,
        limit=1,
    )
    assert [event.content for event in next_page.items] == ["hi"]

    with pytest.raises(ConversationIdempotencyError):
        repository.append_event(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content="different",
            idempotency_key="request-1",
        )


def test_concurrent_appends_allocate_unique_contiguous_sequences(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Concurrent")

    def append(index: int) -> int:
        event = repository.append_event(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content=f"message {index}",
            idempotency_key=f"request-{index}",
        )
        return event.sequence_no

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequence_numbers = list(executor.map(append, range(20)))

    assert sorted(sequence_numbers) == list(range(1, 21))
    events = repository.list_events(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        limit=50,
    )
    assert [event.sequence_no for event in events.items] == list(range(1, 21))


def test_cross_user_append_and_list_are_denied(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Private")

    with pytest.raises(LookupError):
        repository.append_event(
            conversation_id=conversation.conversation_id,
            user_id="other",
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content="not allowed",
            idempotency_key="cross-owner",
        )
    assert not repository.list_events(
        conversation_id=conversation.conversation_id,
        user_id="other",
    ).items


def test_metadata_updates_use_optimistic_versions(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Original")
    updated = repository.update_metadata(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        expected_version=conversation.version,
        title="Renamed",
    )

    assert updated is not None
    assert updated.title == "Renamed"
    assert updated.version == 2
    with pytest.raises(ConversationConcurrencyError):
        repository.update_metadata(
            conversation_id=conversation.conversation_id,
            user_id="owner",
            expected_version=conversation.version,
            title="Stale",
        )


def test_proposals_are_owner_scoped_deduplicated_and_consumed_once(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Proposal")
    now = datetime.now(UTC)
    proposal = ModuleProposal(
        proposal_id="proposal-1",
        conversation_id=conversation.conversation_id,
        user_id="owner",
        proposed_module=ModuleType.ROLEPLAY,
        reason_code=ModuleProposalReason.EXPLICIT_PRACTICE_REQUEST,
        bounded_parameters=RoleplayParameters(
            scenario_description="课堂发言练习"
        ),
        request_hash="a" * 64,
        expires_at=now + timedelta(minutes=10),
        created_at=now,
    )

    assert repository.save_proposal(proposal) == proposal
    replay = repository.save_proposal(
        proposal.model_copy(update={"proposal_id": "proposal-2"})
    )
    assert replay.proposal_id == "proposal-1"
    accepted = repository.transition_proposal(
        proposal_id=proposal.proposal_id,
        conversation_id=conversation.conversation_id,
        user_id="owner",
        expected_status=ModuleProposalStatus.PENDING,
        target_status=ModuleProposalStatus.ACCEPTED,
    )
    assert accepted is not None
    assert accepted.status is ModuleProposalStatus.ACCEPTED
    with pytest.raises(ConversationConcurrencyError):
        repository.transition_proposal(
            proposal_id=proposal.proposal_id,
            conversation_id=conversation.conversation_id,
            user_id="owner",
            expected_status=ModuleProposalStatus.PENDING,
            target_status=ModuleProposalStatus.REJECTED,
        )
    assert (
        repository.get_proposal_for_user(
            proposal_id=proposal.proposal_id,
            conversation_id=conversation.conversation_id,
            user_id="other",
        )
        is None
    )


def test_aes_gcm_protector_authenticates_content_and_metadata() -> None:
    protector = AESGCMConversationContentProtector(
        key=b"k" * 32,
        key_version="test-v1",
    )
    protected = protector.protect("private text", associated_data=b"event-1")

    assert protected.plaintext is None
    assert protector.recover(
        protected,
        associated_data=b"event-1",
    ) == "private text"
    with pytest.raises(ConversationContentProtectionError):
        protector.recover(protected, associated_data=b"event-2")
    with pytest.raises(ConversationContentProtectionError):
        protector.recover(
            ProtectedContent(
                plaintext=None,
                ciphertext=protected.ciphertext,
                nonce=protected.nonce,
                key_version="unknown",
            ),
            associated_data=b"event-1",
        )


def test_production_content_protection_fails_closed_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.delenv("SOCIALEASE_CONVERSATION_CONTENT_KEY", raising=False)
    monkeypatch.delenv(
        "SOCIALEASE_CONVERSATION_CONTENT_KEY_VERSION",
        raising=False,
    )

    with pytest.raises(ConversationContentProtectionError):
        configured_content_protector()
