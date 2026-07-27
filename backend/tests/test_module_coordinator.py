"""Tests for nested module push, pop, resume, and explicit termination."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.conversation.adapters.base import ModuleAdapterResult
from app.conversation.module_coordinator import ModuleCoordinator
from app.conversation.module_policy import ConversationStateError
from app.conversation.repository import SQLiteConversationRepository
from app.models_conversation import (
    ExposureMessageEventPayload,
    ExposureParameters,
    HISTORY_NOTICE_VERSION,
    ModuleProposal,
    ModuleProposalReason,
    ModuleProposalStatus,
    ModuleRun,
    ModuleRunStatus,
    ModuleType,
    ResourceParameters,
    RoleplayMessageEventPayload,
    RoleplayParameters,
)


class RecordingAdapter:
    """Deterministic adapter test double with lifecycle observations."""

    def __init__(self, module_type: ModuleType) -> None:
        self.module_type = module_type
        self.actions: list[str] = []

    async def start(self, run: ModuleRun) -> ModuleAdapterResult:
        self.actions.append(f"start:{run.module_run_id}")
        return self._result(run, "started")

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
    ) -> ModuleAdapterResult:
        self.actions.append(f"message:{message}")
        return self._result(run, f"reply:{message}")

    async def suspend(self, run: ModuleRun) -> None:
        self.actions.append(f"suspend:{run.module_run_id}")

    async def resume(self, run: ModuleRun) -> None:
        self.actions.append(f"resume:{run.module_run_id}")

    async def terminate(self, run: ModuleRun) -> None:
        self.actions.append(f"terminate:{run.module_run_id}")

    def _result(self, run: ModuleRun, response: str) -> ModuleAdapterResult:
        session_id = run.domain_session_id or f"domain-{run.module_run_id}"
        payload = (
            RoleplayMessageEventPayload(session_id=session_id)
            if self.module_type == ModuleType.ROLEPLAY
            else ExposureMessageEventPayload(plan_id=session_id)
        )
        return ModuleAdapterResult(
            response=response,
            domain_session_id=session_id,
            event_payload=payload,
        )


class FailingTerminateAdapter(RecordingAdapter):
    """Simulate an unavailable legacy runtime during crisis escalation."""

    async def terminate(self, run: ModuleRun) -> None:
        self.actions.append(f"terminate-failed:{run.module_run_id}")
        raise RuntimeError("runtime unavailable")


@pytest.fixture
def repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> SQLiteConversationRepository:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "modules.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    return SQLiteConversationRepository()


@pytest.mark.asyncio
async def test_roleplay_exposure_nested_push_pop_and_resume(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(
        user_id="owner",
        title="Nested",
        history_notice_version=HISTORY_NOTICE_VERSION,
    )
    roleplay_adapter = RecordingAdapter(ModuleType.ROLEPLAY)
    exposure_adapter = RecordingAdapter(ModuleType.EXPOSURE)
    coordinator = ModuleCoordinator(
        repository=repository,
        adapters={
            ModuleType.ROLEPLAY: roleplay_adapter,
            ModuleType.EXPOSURE: exposure_adapter,
        },
    )
    roleplay_proposal = _proposal(
        conversation_id=conversation.conversation_id,
        proposal_id="proposal-roleplay",
        module_type=ModuleType.ROLEPLAY,
    )
    exposure_proposal = _proposal(
        conversation_id=conversation.conversation_id,
        proposal_id="proposal-exposure",
        module_type=ModuleType.EXPOSURE,
    )
    repository.save_proposal(roleplay_proposal)
    first = await coordinator.accept(roleplay_proposal)
    repository.save_proposal(exposure_proposal)
    nested = await coordinator.accept(exposure_proposal)

    assert [run.module_type for run in nested.active_module_stack] == [
        ModuleType.ROLEPLAY,
        ModuleType.EXPOSURE,
    ]
    assert [run.status for run in nested.active_module_stack] == [
        ModuleRunStatus.SUSPENDED,
        ModuleRunStatus.ACTIVE,
    ]
    assert first.conversation.conversation_id == conversation.conversation_id
    assert any(action.startswith("suspend:") for action in roleplay_adapter.actions)

    child = nested.active_module_stack[-1]
    popped = await coordinator.terminate_current(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        module_run_id=child.module_run_id,
    )

    assert len(popped.active_module_stack) == 1
    assert popped.active_module_stack[0].status is ModuleRunStatus.ACTIVE
    assert any(action.startswith("resume:") for action in roleplay_adapter.actions)
    assert popped.conversation.active_module_depth == 1

    finished = await coordinator.terminate_all(
        conversation_id=conversation.conversation_id,
        user_id="owner",
    )
    assert finished.active_module_stack == []
    assert finished.conversation.active_module_depth == 0


@pytest.mark.asyncio
async def test_accept_is_idempotent_and_illegal_nesting_stays_pending(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Policy")
    roleplay_adapter = RecordingAdapter(ModuleType.ROLEPLAY)
    resource_adapter = RecordingAdapter(ModuleType.RESOURCE)
    coordinator = ModuleCoordinator(
        repository=repository,
        adapters={
            ModuleType.ROLEPLAY: roleplay_adapter,
            ModuleType.RESOURCE: resource_adapter,
        },
    )
    roleplay = _proposal(
        conversation_id=conversation.conversation_id,
        proposal_id="proposal-roleplay",
        module_type=ModuleType.ROLEPLAY,
    )
    repository.save_proposal(roleplay)
    first = await coordinator.accept(roleplay)
    replay = await coordinator.accept(
        roleplay.model_copy(update={"status": ModuleProposalStatus.ACCEPTED})
    )
    assert len(first.active_module_stack) == 1
    assert len(replay.active_module_stack) == 1
    assert len(
        [action for action in roleplay_adapter.actions if action.startswith("start:")]
    ) == 1

    resource = _proposal(
        conversation_id=conversation.conversation_id,
        proposal_id="proposal-resource",
        module_type=ModuleType.RESOURCE,
    )
    repository.save_proposal(resource)
    with pytest.raises(ConversationStateError):
        await coordinator.accept(resource)
    stored = repository.get_proposal_for_user(
        proposal_id=resource.proposal_id,
        conversation_id=conversation.conversation_id,
        user_id="owner",
    )
    assert stored is not None
    assert stored.status is ModuleProposalStatus.PENDING


@pytest.mark.asyncio
async def test_active_module_receives_messages_on_same_timeline(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Message")
    adapter = RecordingAdapter(ModuleType.ROLEPLAY)
    coordinator = ModuleCoordinator(
        repository=repository,
        adapters={ModuleType.ROLEPLAY: adapter},
    )
    proposal = _proposal(
        conversation_id=conversation.conversation_id,
        proposal_id="proposal-roleplay-message",
        module_type=ModuleType.ROLEPLAY,
    )
    repository.save_proposal(proposal)
    started = await coordinator.accept(proposal)
    result, event = await coordinator.handle_message(
        conversation_id=conversation.conversation_id,
        user_id="owner",
        message="我的练习回复",
        idempotency_key="turn-001",
    )

    assert result.response == "reply:我的练习回复"
    assert event.module_run_id == started.active_module_stack[-1].module_run_id
    assert event.sequence_no == 3


@pytest.mark.asyncio
async def test_crisis_preemption_stops_nested_stack_even_if_runtime_fails(
    repository: SQLiteConversationRepository,
) -> None:
    conversation = repository.create(user_id="owner", title="Crisis nested")
    roleplay_adapter = RecordingAdapter(ModuleType.ROLEPLAY)
    exposure_adapter = FailingTerminateAdapter(ModuleType.EXPOSURE)
    coordinator = ModuleCoordinator(
        repository=repository,
        adapters={
            ModuleType.ROLEPLAY: roleplay_adapter,
            ModuleType.EXPOSURE: exposure_adapter,
        },
    )
    roleplay = _proposal(
        conversation_id=conversation.conversation_id,
        proposal_id="crisis-roleplay",
        module_type=ModuleType.ROLEPLAY,
    )
    exposure = _proposal(
        conversation_id=conversation.conversation_id,
        proposal_id="crisis-exposure",
        module_type=ModuleType.EXPOSURE,
    )
    repository.save_proposal(roleplay)
    await coordinator.accept(roleplay)
    repository.save_proposal(exposure)
    await coordinator.accept(exposure)

    events = await coordinator.preempt_for_crisis(
        conversation_id=conversation.conversation_id,
        user_id="owner",
    )

    assert len(events) == 2
    assert repository.list_module_stack(
        conversation_id=conversation.conversation_id,
        user_id="owner",
    ) == []
    refreshed = repository.get_for_user(
        conversation.conversation_id,
        "owner",
    )
    assert refreshed is not None
    assert refreshed.active_module_depth == 0


def _proposal(
    *,
    conversation_id: str,
    proposal_id: str,
    module_type: ModuleType,
) -> ModuleProposal:
    parameters = {
        ModuleType.ROLEPLAY: RoleplayParameters(
            scenario_description="小组讨论"
        ),
        ModuleType.EXPOSURE: ExposureParameters(goal="小组讨论开口"),
        ModuleType.RESOURCE: ResourceParameters(query="学校支持资源"),
    }[module_type]
    reason = {
        ModuleType.ROLEPLAY: ModuleProposalReason.EXPLICIT_PRACTICE_REQUEST,
        ModuleType.EXPOSURE: ModuleProposalReason.GRADED_PRACTICE_MAY_HELP,
        ModuleType.RESOURCE: ModuleProposalReason.RESOURCE_LOOKUP_REQUESTED,
    }[module_type]
    now = datetime.now(UTC)
    return ModuleProposal(
        proposal_id=proposal_id,
        conversation_id=conversation_id,
        user_id="owner",
        proposed_module=module_type,
        reason_code=reason,
        bounded_parameters=parameters,
        request_hash=(proposal_id + ("x" * 64))[:64],
        expires_at=now + timedelta(minutes=10),
        created_at=now,
    )
