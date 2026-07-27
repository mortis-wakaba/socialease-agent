"""Tests for unified conversation contracts and module-stack policy."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.conversation.module_policy import ConversationStateError, ModuleStackPolicy
from app.models_conversation import (
    Conversation,
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ConversationStatus,
    CrisisEscalatedEventPayload,
    HISTORY_NOTICE_VERSION,
    MAX_MODULE_DEPTH,
    ModuleProposal,
    ModuleProposalReason,
    ModuleProposalStatus,
    ModuleRun,
    ModuleRunStatus,
    ModuleType,
    RoleplayParameters,
)


NOW = datetime.now(UTC)


def _run(
    run_id: str,
    module_type: ModuleType,
    *,
    depth: int,
    parent: str | None,
    status: ModuleRunStatus,
) -> ModuleRun:
    return ModuleRun(
        module_run_id=run_id,
        conversation_id="conversation-1",
        user_id="user-1",
        module_type=module_type,
        parent_module_run_id=parent,
        depth=depth,
        status=status,
        started_at=NOW,
    )


def test_conversation_contract_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Conversation(
            conversation_id="conversation-1",
            user_id="user-1",
            title="First conversation",
            created_at=NOW,
            updated_at=NOW,
            history_notice_version=HISTORY_NOTICE_VERSION,
            unexpected=True,
        )


def test_proposal_requires_matching_bounded_parameters() -> None:
    proposal = ModuleProposal(
        proposal_id="proposal-1",
        conversation_id="conversation-1",
        user_id="user-1",
        proposed_module=ModuleType.ROLEPLAY,
        reason_code=ModuleProposalReason.EXPLICIT_PRACTICE_REQUEST,
        bounded_parameters=RoleplayParameters(scenario_description="小组讨论"),
        request_hash="a" * 64,
        expires_at=NOW + timedelta(minutes=10),
        created_at=NOW,
    )

    assert proposal.status is ModuleProposalStatus.PENDING
    assert proposal.bounded_parameters.kind == "roleplay"


def test_event_payload_must_match_event_type() -> None:
    with pytest.raises(ValidationError):
        ConversationEvent(
            event_id="event-1",
            conversation_id="conversation-1",
            user_id="user-1",
            sequence_no=1,
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content="hello",
            structured_payload=CrisisEscalatedEventPayload(),
            idempotency_key="message-1",
            created_at=NOW,
        )


def test_state_transitions_are_one_way_and_explicit() -> None:
    ModuleStackPolicy.validate_conversation_transition(
        ConversationStatus.ACTIVE,
        ConversationStatus.ARCHIVED,
    )
    ModuleStackPolicy.validate_proposal_transition(
        ModuleProposalStatus.PENDING,
        ModuleProposalStatus.ACCEPTED,
    )
    ModuleStackPolicy.validate_run_transition(
        ModuleRunStatus.ACTIVE,
        ModuleRunStatus.SUSPENDED,
    )

    with pytest.raises(ConversationStateError):
        ModuleStackPolicy.validate_proposal_transition(
            ModuleProposalStatus.ACCEPTED,
            ModuleProposalStatus.REJECTED,
        )
    with pytest.raises(ConversationStateError):
        ModuleStackPolicy.validate_run_transition(
            ModuleRunStatus.COMPLETED,
            ModuleRunStatus.ACTIVE,
        )


def test_roleplay_can_nest_exposure_but_resource_cannot_nest() -> None:
    roleplay = _run(
        "roleplay-1",
        ModuleType.ROLEPLAY,
        depth=1,
        parent=None,
        status=ModuleRunStatus.ACTIVE,
    )
    ModuleStackPolicy.validate_push([roleplay], ModuleType.EXPOSURE)

    resource = _run(
        "resource-1",
        ModuleType.RESOURCE,
        depth=1,
        parent=None,
        status=ModuleRunStatus.ACTIVE,
    )
    with pytest.raises(ConversationStateError):
        ModuleStackPolicy.validate_push([resource], ModuleType.ROLEPLAY)


def test_stack_rejects_depth_overflow_and_cycles() -> None:
    stack = [
        _run(
            "roleplay-1",
            ModuleType.ROLEPLAY,
            depth=1,
            parent=None,
            status=ModuleRunStatus.SUSPENDED,
        ),
        _run(
            "worksheet-1",
            ModuleType.WORKSHEET,
            depth=2,
            parent="roleplay-1",
            status=ModuleRunStatus.SUSPENDED,
        ),
        _run(
            "roleplay-2",
            ModuleType.ROLEPLAY,
            depth=MAX_MODULE_DEPTH,
            parent="worksheet-1",
            status=ModuleRunStatus.ACTIVE,
        ),
    ]
    with pytest.raises(ConversationStateError, match="maximum"):
        ModuleStackPolicy.validate_push(stack, ModuleType.EXPOSURE)

    cycle = [
        _run(
            "same-run",
            ModuleType.ROLEPLAY,
            depth=1,
            parent=None,
            status=ModuleRunStatus.SUSPENDED,
        ),
        _run(
            "same-run",
            ModuleType.EXPOSURE,
            depth=2,
            parent="same-run",
            status=ModuleRunStatus.ACTIVE,
        ),
    ]
    with pytest.raises(ConversationStateError, match="cycle"):
        ModuleStackPolicy.validate_push(cycle, ModuleType.ROLEPLAY)


def test_crisis_always_preempts_module_routing() -> None:
    assert ModuleStackPolicy.safety_preempts_modules(
        crisis=True,
        has_active_module=True,
    )
    assert not ModuleStackPolicy.safety_preempts_modules(
        crisis=False,
        has_active_module=True,
    )
