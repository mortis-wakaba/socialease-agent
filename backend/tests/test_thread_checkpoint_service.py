"""Production contracts for durable, token-bounded thread checkpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.factory import repository_factory
from app.memory.long_term_repository import MemoryConflictError
from app.memory.thread_checkpoint_service import ThreadCheckpointService
from app.memory.token_estimator import ConservativeTokenEstimator
from app.models_long_term_memory import PracticeThreadStatus
from app.models_memory import UserConsentState
from app.models_roleplay import RoleplayScenario


NOW = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


class ConflictOnceRepository:
    """Delegate repository that simulates one optimistic-lock race."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.conflicts = 0

    def save_checkpoint(self, checkpoint, **kwargs):
        if self.conflicts == 0:
            self.conflicts += 1
            raise MemoryConflictError("simulated concurrent writer")
        return self.delegate.save_checkpoint(checkpoint, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


def _enable_consent(user_id: str) -> None:
    repository_factory().user_memory_settings_repository().save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_practice_summary=True),
    )


def _service(
    *,
    repository=None,
    token_budget: int = 256,
    restore_ttl: timedelta = timedelta(days=180),
) -> ThreadCheckpointService:
    return ThreadCheckpointService(
        repository=repository
        or repository_factory().long_term_memory_repository(),
        settings_repository=repository_factory().user_memory_settings_repository(),
        token_estimator=ConservativeTokenEstimator(),
        active_memory_token_budget=token_budget,
        restore_ttl=restore_ttl,
    )


def test_checkpoint_lifecycle_is_versioned_minimized_and_audited() -> None:
    user_id = f"checkpoint_lifecycle_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    repository = repository_factory().long_term_memory_repository()
    service = _service(repository=repository)

    started = service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=RoleplayScenario.DORM_CONFLICT,
        current_stage="roleplay_started",
        status=PracticeThreadStatus.ACTIVE,
        reason_code="roleplay_started",
        unresolved_next_step="联系 13912345678 后继续练习。",
        changed_at=NOW,
    )
    progressed = service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=RoleplayScenario.DORM_CONFLICT,
        current_stage="practice_turn_completed",
        status=PracticeThreadStatus.ACTIVE,
        reason_code="practice_turn_completed",
        helpful_strategy_codes=["clear_request", "boundary_statement"],
        unresolved_next_step="继续当前练习。",
        changed_at=NOW + timedelta(minutes=1),
        touch_if_unchanged=True,
    )
    paused = service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=RoleplayScenario.DORM_CONFLICT,
        current_stage="paused",
        status=PracticeThreadStatus.PAUSED,
        reason_code="practice_paused",
        unresolved_next_step="恢复后继续当前角色扮演练习。",
        changed_at=NOW + timedelta(minutes=2),
    )

    assert started is not None
    assert progressed is not None
    assert paused is not None
    assert [started.version, progressed.version, paused.version] == [1, 2, 3]
    assert "13912345678" not in started.unresolved_next_step
    assert progressed.helpful_strategy_codes == [
        "clear_request",
        "boundary_statement",
    ]
    events = repository.list_events(user_id=user_id, subject_id=thread_id)
    assert [event.reason_code for event in events] == [
        "roleplay_started",
        "practice_turn_completed",
        "practice_paused",
    ]
    assert all(event.summary is None for event in events)


def test_restore_requires_consent_exact_owner_thread_scenario_status_and_ttl() -> None:
    owner = f"checkpoint_restore_{uuid4().hex}"
    other_user = f"checkpoint_other_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    service = _service(restore_ttl=timedelta(days=30))
    service.record_roleplay(
        user_id=owner,
        thread_id=thread_id,
        scenario=RoleplayScenario.GROUP_DISCUSSION,
        current_stage="paused",
        status=PracticeThreadStatus.PAUSED,
        reason_code="practice_paused",
        unresolved_next_step="恢复后先表达一个核心观点。",
        changed_at=NOW,
    )

    assert (
        service.restore_roleplay_context(
            user_id=owner,
            thread_id=thread_id,
            expected_scenario=RoleplayScenario.GROUP_DISCUSSION,
            now=NOW + timedelta(days=1),
        )
        is None
    )
    _enable_consent(owner)
    assert (
        service.restore_roleplay_context(
            user_id=other_user,
            thread_id=thread_id,
            expected_scenario=RoleplayScenario.GROUP_DISCUSSION,
            now=NOW + timedelta(days=1),
        )
        is None
    )
    assert (
        service.restore_roleplay_context(
            user_id=owner,
            thread_id=thread_id,
            expected_scenario=RoleplayScenario.DORM_CONFLICT,
            now=NOW + timedelta(days=1),
        )
        is None
    )
    restored = service.restore_roleplay_context(
        user_id=owner,
        thread_id=thread_id,
        expected_scenario=RoleplayScenario.GROUP_DISCUSSION,
        now=NOW + timedelta(days=1),
    )
    assert restored is not None
    assert "group_discussion" in (restored.compact_state.current_topic or "")
    assert (
        service.restore_roleplay_context(
            user_id=owner,
            thread_id=thread_id,
            expected_scenario=RoleplayScenario.GROUP_DISCUSSION,
            now=NOW + timedelta(days=31),
        )
        is None
    )

    service.record_roleplay(
        user_id=owner,
        thread_id=thread_id,
        scenario=RoleplayScenario.GROUP_DISCUSSION,
        current_stage="feedback_completed",
        status=PracticeThreadStatus.COMPLETED,
        reason_code="feedback_completed",
        unresolved_next_step=None,
        changed_at=NOW + timedelta(days=2),
    )
    assert (
        service.restore_roleplay_context(
            user_id=owner,
            thread_id=thread_id,
            expected_scenario=RoleplayScenario.GROUP_DISCUSSION,
            now=NOW + timedelta(days=2),
        )
        is None
    )


def test_active_checkpoint_has_independent_hard_token_budget() -> None:
    user_id = f"checkpoint_budget_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    _enable_consent(user_id)
    service = _service(token_budget=128)
    service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=RoleplayScenario.EXPRESS_DISAGREEMENT,
        current_stage="practice_turn_completed",
        status=PracticeThreadStatus.ACTIVE,
        reason_code="practice_turn_completed",
        helpful_strategy_codes=[
            "reason_given",
            "clear_request",
            "boundary_statement",
            "empathy_marker",
            "specific_anchor",
            "collaborative_offer",
        ],
        unresolved_next_step="继续练习表达不同意见，并补充一个清楚、具体且简短的理由。" * 3,
        changed_at=NOW,
    )

    restored = service.restore_roleplay_context(
        user_id=user_id,
        thread_id=thread_id,
        expected_scenario=RoleplayScenario.EXPRESS_DISAGREEMENT,
        now=NOW,
    )

    assert restored is not None
    assert restored.token_budget == 128
    assert restored.estimated_tokens <= restored.token_budget


def test_checkpoint_write_retries_one_conflict_and_blocks_terminal_reactivation() -> None:
    user_id = f"checkpoint_retry_{uuid4().hex}"
    thread_id = f"thread_{uuid4().hex}"
    delegate = repository_factory().long_term_memory_repository()
    repository = ConflictOnceRepository(delegate)
    service = _service(repository=repository)

    completed = service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=RoleplayScenario.CLASSROOM_SPEECH,
        current_stage="feedback_completed",
        status=PracticeThreadStatus.COMPLETED,
        reason_code="feedback_completed",
        changed_at=NOW,
    )
    invalid = service.record_roleplay(
        user_id=user_id,
        thread_id=thread_id,
        scenario=RoleplayScenario.CLASSROOM_SPEECH,
        current_stage="resumed",
        status=PracticeThreadStatus.ACTIVE,
        reason_code="practice_resumed",
        changed_at=NOW + timedelta(minutes=1),
    )

    assert repository.conflicts == 1
    assert completed is not None
    assert invalid is None
    assert delegate.get_checkpoint(thread_id, user_id).status == (
        PracticeThreadStatus.COMPLETED
    )
