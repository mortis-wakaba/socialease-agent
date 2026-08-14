"""Unit tests for deterministic intent queue cancellation."""

from datetime import UTC, datetime

from app.conversation.intent_queue import (
    IntentQueueControlAction,
    activate_current_consented_step,
    activate_current_ready_step,
    apply_intent_queue_control,
    complete_current_intent_step,
    detect_intent_queue_control,
    latest_applied_calendar_effect_step,
    undo_calendar_step_effect,
)
from app.models import (
    Intent,
    IntentAuthorizationBasis,
    IntentEffectStatus,
    IntentExecutionPlan,
    IntentExecutionStep,
    IntentRelation,
    IntentStepStatus,
    IntentStepEffect,
)
from app.models_intent_plan import (
    ConversationIntentPlan,
    ConversationIntentPlanStatus,
)


def _plan() -> ConversationIntentPlan:
    now = datetime.now(UTC)
    return ConversationIntentPlan(
        plan_id="plan-1",
        conversation_id="conversation-1",
        user_id="user-1",
        source_event_id="event-1",
        execution_plan=IntentExecutionPlan(
            current_intent=Intent.PROGRESS_REVIEW,
            pending_intents=[
                Intent.CALENDAR_PLANNING,
                Intent.ROLEPLAY_PRACTICE,
            ],
            steps=[
                IntentExecutionStep(
                    step_id="step-1",
                    intent=Intent.PROGRESS_REVIEW,
                    authorization_basis=(
                        IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                    ),
                    status=IntentStepStatus.READY,
                ),
                IntentExecutionStep(
                    step_id="step-2",
                    intent=Intent.CALENDAR_PLANNING,
                    relation=IntentRelation.AFTER_PRIMARY,
                    authorization_basis=(
                        IntentAuthorizationBasis.EXPLICIT_CURRENT_REQUEST
                    ),
                    status=IntentStepStatus.READY_WITH_UNDO,
                    undo_supported=True,
                ),
                IntentExecutionStep(
                    step_id="step-3",
                    intent=Intent.ROLEPLAY_PRACTICE,
                    relation=IntentRelation.AFTER_PRIMARY,
                    authorization_basis=IntentAuthorizationBasis.INFERRED,
                    status=IntentStepStatus.AWAITING_CONSENT,
                ),
            ],
        ),
        created_at=now,
        updated_at=now,
    )


def test_detects_pending_and_specific_cancellation() -> None:
    pending = detect_intent_queue_control("当前继续，后面的都不用做了")
    specific = detect_intent_queue_control("提醒不用设置了")

    assert pending is not None
    assert pending.action == IntentQueueControlAction.CANCEL_PENDING
    assert specific is not None
    assert specific.action == IntentQueueControlAction.CANCEL_INTENT
    assert specific.target_intent == Intent.CALENDAR_PLANNING


def test_cancel_pending_keeps_current_and_cancels_rest() -> None:
    control = detect_intent_queue_control("取消后续任务")
    assert control is not None

    updated, response = apply_intent_queue_control(_plan(), control)

    assert updated.status == ConversationIntentPlanStatus.ACTIVE
    assert updated.execution_plan.current_intent == Intent.PROGRESS_REVIEW
    assert updated.execution_plan.pending_intents == []
    assert [step.status for step in updated.execution_plan.steps] == [
        IntentStepStatus.READY,
        IntentStepStatus.CANCELLED,
        IntentStepStatus.CANCELLED,
    ]
    assert "后续 2 个" in response


def test_cancel_pending_cancels_promoted_unstarted_secondary() -> None:
    plan = _plan()
    plan.execution_plan.steps[0].status = IntentStepStatus.COMPLETED
    plan.execution_plan.current_intent = Intent.CALENDAR_PLANNING
    plan.execution_plan.pending_intents = [Intent.ROLEPLAY_PRACTICE]
    control = detect_intent_queue_control("当前继续，后面的都不用做了")
    assert control is not None

    updated, response = apply_intent_queue_control(plan, control)

    assert updated.execution_plan.pending_intents == []
    assert updated.status == ConversationIntentPlanStatus.COMPLETED
    assert [step.status for step in updated.execution_plan.steps] == [
        IntentStepStatus.COMPLETED,
        IntentStepStatus.CANCELLED,
        IntentStepStatus.CANCELLED,
    ]
    assert "后续 2 个" in response


def test_cancel_current_advances_to_next_live_step() -> None:
    control = detect_intent_queue_control("取消当前任务")
    assert control is not None

    updated, _ = apply_intent_queue_control(_plan(), control)

    assert updated.execution_plan.current_intent == Intent.CALENDAR_PLANNING
    assert updated.execution_plan.pending_intents == [Intent.ROLEPLAY_PRACTICE]
    assert updated.execution_plan.steps[0].status == IntentStepStatus.CANCELLED


def test_cancel_specific_and_all_are_bounded_to_live_steps() -> None:
    specific = detect_intent_queue_control("不要角色扮演了")
    assert specific is not None
    updated, _ = apply_intent_queue_control(_plan(), specific)
    assert updated.execution_plan.steps[2].status == IntentStepStatus.CANCELLED

    all_control = detect_intent_queue_control("全部取消")
    assert all_control is not None
    cancelled, _ = apply_intent_queue_control(updated, all_control)
    assert cancelled.status == ConversationIntentPlanStatus.CANCELLED
    assert all(
        step.status == IntentStepStatus.CANCELLED
        for step in cancelled.execution_plan.steps
    )


def test_completion_advances_then_activation_is_explicit() -> None:
    completed = complete_current_intent_step(
        _plan(),
        expected_intent=Intent.PROGRESS_REVIEW,
    )

    assert completed.execution_plan.steps[0].status == IntentStepStatus.COMPLETED
    assert completed.execution_plan.current_intent == Intent.CALENDAR_PLANNING
    assert completed.execution_plan.pending_intents == [Intent.ROLEPLAY_PRACTICE]
    unchanged = activate_current_ready_step(
        completed,
        expected_step_id="step-2",
    )
    assert unchanged is completed

    completed.execution_plan.steps[1].status = IntentStepStatus.READY
    activated = activate_current_ready_step(
        completed,
        expected_step_id="step-2",
    )
    assert activated.execution_plan.steps[1].status == IntentStepStatus.ACTIVE


def test_completion_rejects_stale_intent_and_finishes_last_step() -> None:
    plan = _plan()
    stale = complete_current_intent_step(
        plan,
        expected_intent=Intent.ROLEPLAY_PRACTICE,
    )
    assert stale is plan

    for step in plan.execution_plan.steps[:2]:
        step.status = IntentStepStatus.COMPLETED
    plan.execution_plan.current_intent = Intent.ROLEPLAY_PRACTICE
    finished = complete_current_intent_step(
        plan,
        expected_intent=Intent.ROLEPLAY_PRACTICE,
    )
    assert finished.status == ConversationIntentPlanStatus.COMPLETED


def test_consent_only_activates_matching_awaiting_step() -> None:
    plan = _plan()
    plan.execution_plan.steps[0].status = IntentStepStatus.AWAITING_CONSENT
    activated = activate_current_consented_step(
        plan,
        expected_intent=Intent.PROGRESS_REVIEW,
    )
    assert activated.execution_plan.steps[0].status == IntentStepStatus.ACTIVE

    stale = activate_current_consented_step(
        plan,
        expected_intent=Intent.ROLEPLAY_PRACTICE,
    )
    assert stale is plan


def test_reversible_completion_persists_receipt_and_can_be_undone() -> None:
    plan = _plan()
    plan.execution_plan.steps[0].status = IntentStepStatus.COMPLETED
    plan.execution_plan.steps[1].status = IntentStepStatus.READY_WITH_UNDO
    plan.execution_plan.current_intent = Intent.CALENDAR_PLANNING
    effect = IntentStepEffect(
        external_action_id="calendar-action-1",
        idempotency_key="calendar-idempotency-1",
        applied_at=datetime.now(UTC),
    )

    completed = complete_current_intent_step(
        plan,
        expected_intent=Intent.CALENDAR_PLANNING,
        effect=effect,
    )

    receipt = latest_applied_calendar_effect_step(completed)
    assert receipt is not None
    assert receipt.effect == effect
    undone = undo_calendar_step_effect(completed, step_id=receipt.step_id)
    assert undone.execution_plan.steps[1].effect is not None
    assert (
        undone.execution_plan.steps[1].effect.status
        == IntentEffectStatus.UNDONE
    )
    assert undone.execution_plan.steps[1].effect.undone_at is not None
