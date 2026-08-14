"""Unit tests for asynchronous intent-effect ledger reconciliation."""

from datetime import UTC, datetime

import pytest

from app.calendar.outbox import CalendarActionJob
from app.conversation.effect_reconciler import IntentEffectReconciler
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
from app.models_intent_effect import (
    IntentEffectLedgerRecord,
    IntentEffectLedgerStatus,
)
from app.models_intent_plan import ConversationIntentPlan


def _pending_effect() -> IntentEffectLedgerRecord:
    now = datetime.now(UTC)
    return IntentEffectLedgerRecord(
        effect_id="effect-1",
        plan_id="plan-1",
        step_id="calendar-step",
        conversation_id="conversation-1",
        user_id="owner",
        kind="calendar_event",
        status=IntentEffectLedgerStatus.APPLY_PENDING,
        create_job_id="create-job-1",
        create_idempotency_key="calendar-create-key",
        created_at=now,
        updated_at=now,
    )


def _plan() -> ConversationIntentPlan:
    now = datetime.now(UTC)
    return ConversationIntentPlan(
        plan_id="plan-1",
        conversation_id="conversation-1",
        user_id="owner",
        source_event_id="event-1",
        execution_plan=IntentExecutionPlan(
            current_intent=Intent.CALENDAR_PLANNING,
            steps=[
                IntentExecutionStep(
                    step_id="calendar-step",
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


class FakeRepository:
    def __init__(self) -> None:
        self.effect = _pending_effect()
        self.plan = _plan()

    async def list_pending_intent_effects(self, *, limit: int):
        del limit
        return [self.effect]

    async def update_intent_effect(self, effect, *, expected_version: int):
        assert expected_version == self.effect.version
        self.effect = effect.model_copy(update={"version": expected_version + 1})
        return self.effect

    async def get_intent_plan(self, *, conversation_id: str, user_id: str):
        assert conversation_id == self.plan.conversation_id
        assert user_id == self.plan.user_id
        return self.plan

    async def update_intent_plan(self, plan, *, expected_version: int):
        assert expected_version == self.plan.version
        self.plan = plan.model_copy(update={"version": expected_version + 1})
        return self.plan


class FakeOutbox:
    async def get(self, job_id: str) -> CalendarActionJob:
        proposal = CalendarEventProposal(
            title="15分钟练习",
            start_time=datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
            duration_minutes=15,
        )
        now = datetime.now(UTC)
        response = CalendarEventResponse(
            event=CalendarEventRecord(
                calendar_action_id="calendar-action-1",
                provider_event_id="provider-1",
                user_id="owner",
                proposal=proposal,
                idempotency_key="calendar-create-key",
                created_at=now,
                updated_at=now,
            ),
            verified=True,
            tool_trace=CalendarToolTrace(
                tool_name="create",
                transport="fake",
                status="success",
                latency_ms=0,
            ),
        )
        return CalendarActionJob(
            job_id=job_id,
            protocol_id=None,
            user_id="owner",
            action_type="create",
            request_hash="a" * 64,
            idempotency_key="calendar-create-key",
            payload={},
            status="completed",
            attempt_count=1,
            max_attempts=8,
            result=response.model_dump(mode="json"),
        )


@pytest.mark.anyio
async def test_reconciler_applies_ledger_and_completes_matching_plan() -> None:
    repository = FakeRepository()
    reconciler = IntentEffectReconciler(
        repository=repository,  # type: ignore[arg-type]
        outbox=FakeOutbox(),  # type: ignore[arg-type]
    )

    count = await reconciler.run_once()

    assert count == 1
    assert repository.effect.status == IntentEffectLedgerStatus.APPLIED
    assert repository.effect.external_action_id == "calendar-action-1"
    step = repository.plan.execution_plan.steps[0]
    assert step.status == IntentStepStatus.COMPLETED
    assert step.effect is not None
