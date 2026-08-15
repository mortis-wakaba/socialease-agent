"""Unit tests for reversible intent side-effect execution."""

from datetime import UTC, datetime

import pytest

from app.conversation.intent_effect_executor import IntentEffectExecutor
from app.conversation.reversible_actions import (
    ReversibleActionExecution,
    ReversibleActionPending,
)
from app.models import (
    Intent,
    IntentAuthorizationBasis,
    IntentExecutionPlan,
    IntentExecutionStep,
    IntentStepStatus,
)
from app.models_calendar import (
    CalendarEventProposal,
    CalendarEventRecord,
    CalendarEventResponse,
    CalendarToolTrace,
)
from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
)
from app.models_intent_effect import (
    IntentEffectLedgerRecord,
    IntentEffectLedgerStatus,
)
from app.models_intent_plan import ConversationIntentPlan


class EffectRepository:
    """Minimal idempotent repository fake for executor state transitions."""

    def __init__(self) -> None:
        self.effects: dict[tuple[str, str], IntentEffectLedgerRecord] = {}
        self.events: list[ConversationEvent] = []
        self.plan_updates = 0

    async def save_intent_effect(
        self,
        effect: IntentEffectLedgerRecord,
    ) -> IntentEffectLedgerRecord:
        return self.effects.setdefault((effect.plan_id, effect.step_id), effect)

    async def update_intent_effect(
        self,
        effect: IntentEffectLedgerRecord,
        *,
        expected_version: int,
    ) -> IntentEffectLedgerRecord:
        assert effect.version == expected_version
        updated = effect.model_copy(update={"version": effect.version + 1})
        self.effects[(effect.plan_id, effect.step_id)] = updated
        return updated

    async def update_intent_plan(
        self,
        plan: ConversationIntentPlan,
        *,
        expected_version: int,
    ) -> ConversationIntentPlan:
        assert plan.version == expected_version
        self.plan_updates += 1
        return plan.model_copy(update={"version": plan.version + 1})

    async def append_event(self, **values: object) -> ConversationEvent:
        event = ConversationEvent(
            event_id=f"event-{len(self.events) + 2}",
            sequence_no=len(self.events) + 2,
            created_at=datetime.now(UTC),
            **values,
        )
        self.events.append(event)
        return event


class CalendarExecutor:
    """Return a verified action, optionally after one durable pending result."""

    def __init__(self, *, pending_first: bool = False) -> None:
        self.pending_first = pending_first
        self.calls: list[str] = []

    async def create(
        self,
        *,
        user_id: str,
        proposal: CalendarEventProposal,
        idempotency_key: str,
    ) -> ReversibleActionExecution:
        self.calls.append(idempotency_key)
        if self.pending_first and len(self.calls) == 1:
            raise ReversibleActionPending("calendar-job-1")
        now = datetime.now(UTC)
        return ReversibleActionExecution(
            job_id="calendar-job-1",
            response=CalendarEventResponse(
                event=CalendarEventRecord(
                    calendar_action_id="calendar-action-1",
                    provider_event_id="provider-event-1",
                    user_id=user_id,
                    proposal=proposal,
                    idempotency_key=idempotency_key,
                    created_at=now,
                    updated_at=now,
                ),
                verified=True,
                tool_trace=CalendarToolTrace(
                    tool_name="calendar-test",
                    transport="fake",
                    status="success",
                    latency_ms=0,
                    read_after_write_verified=True,
                ),
            ),
        )


def _proposal(_: str) -> CalendarEventProposal:
    return CalendarEventProposal(
        title="15分钟练习",
        start_time=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        duration_minutes=15,
    )


def _plan() -> ConversationIntentPlan:
    now = datetime.now(UTC)
    return ConversationIntentPlan(
        plan_id="plan-1",
        conversation_id="conversation-1",
        user_id="user-1",
        source_event_id="event-1",
        execution_plan=IntentExecutionPlan(
            current_intent=Intent.CALENDAR_PLANNING,
            steps=[
                IntentExecutionStep(
                    step_id="calendar-step-1",
                    intent=Intent.CALENDAR_PLANNING,
                    authorization_basis=(
                        IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                    ),
                    status=IntentStepStatus.READY_WITH_UNDO,
                    undo_supported=True,
                )
            ],
        ),
        created_at=now,
        updated_at=now,
    )


def _source_event() -> ConversationEvent:
    return ConversationEvent(
        event_id="event-1",
        conversation_id="conversation-1",
        user_id="user-1",
        sequence_no=1,
        event_type=ConversationEventType.USER_MESSAGE,
        role=ConversationEventRole.USER,
        content="明晚八点提醒我练习",
        idempotency_key="user:command-1",
        created_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_success_persists_receipt_event_and_completed_plan() -> None:
    repository = EffectRepository()
    calendar = CalendarExecutor()
    plan = _plan()
    executor = IntentEffectExecutor(
        repository=repository,  # type: ignore[arg-type]
        calendar_executor=calendar,  # type: ignore[arg-type]
        proposal_builder=_proposal,
    )

    result = await executor.execute(
        plan=plan,
        step=plan.execution_plan.steps[0],
        source_event=_source_event(),
    )

    assert result is not None
    assert result.completed is True
    assert result.plan.execution_plan.steps[0].status is IntentStepStatus.COMPLETED
    effect = repository.effects[("plan-1", "calendar-step-1")]
    assert effect.status is IntentEffectLedgerStatus.APPLIED
    assert effect.external_action_id == "calendar-action-1"
    assert repository.plan_updates == 1
    assert "取消提醒" in result.response


@pytest.mark.anyio
async def test_pending_retry_reuses_key_and_promotes_existing_receipt() -> None:
    repository = EffectRepository()
    calendar = CalendarExecutor(pending_first=True)
    plan = _plan()
    executor = IntentEffectExecutor(
        repository=repository,  # type: ignore[arg-type]
        calendar_executor=calendar,  # type: ignore[arg-type]
        proposal_builder=_proposal,
    )

    pending = await executor.execute(
        plan=plan,
        step=plan.execution_plan.steps[0],
        source_event=_source_event(),
    )
    completed = await executor.execute(
        plan=plan,
        step=plan.execution_plan.steps[0],
        source_event=_source_event(),
    )

    assert pending is not None and pending.completed is False
    assert completed is not None and completed.completed is True
    assert len(repository.effects) == 1
    effect = repository.effects[("plan-1", "calendar-step-1")]
    assert effect.status is IntentEffectLedgerStatus.APPLIED
    assert effect.version == 2
    assert calendar.calls == [calendar.calls[0], calendar.calls[0]]
    assert repository.plan_updates == 1
