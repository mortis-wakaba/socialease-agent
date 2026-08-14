"""Deterministic cancellation controls for persistent intent queues."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel

from app.models import (
    Intent,
    IntentEffectStatus,
    IntentExecutionStep,
    IntentRelation,
    IntentStepEffect,
    IntentStepStatus,
)
from app.models_intent_plan import (
    ConversationIntentPlan,
    ConversationIntentPlanStatus,
)


class IntentQueueControlAction(str, Enum):
    """Supported user controls that bypass ordinary semantic routing."""

    CANCEL_CURRENT = "cancel_current"
    CANCEL_PENDING = "cancel_pending"
    CANCEL_ALL = "cancel_all"
    CANCEL_INTENT = "cancel_intent"


class IntentQueueControl(BaseModel):
    """One deterministic queue control extracted from a user message."""

    action: IntentQueueControlAction
    target_intent: Intent | None = None


_INTENT_TERMS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.CALENDAR_PLANNING, ("日历", "提醒", "安排时间")),
    (Intent.ROLEPLAY_PRACTICE, ("角色扮演", "模拟对话", "模拟")),
    (Intent.CBT_WORKSHEET, ("想法记录", "记录表", "表格")),
    (Intent.EXPOSURE_PLANNING, ("暴露", "阶梯", "分级练习")),
    (Intent.CAMPUS_RESOURCE_QUERY, ("校园资源", "心理中心", "资源")),
    (Intent.PROGRESS_REVIEW, ("复盘", "进度")),
)


def detect_intent_queue_control(message: str) -> IntentQueueControl | None:
    """Recognize explicit Chinese cancellation commands without an LLM."""
    normalized = "".join(message.lower().split())
    cancel_markers = ("取消", "不用", "不要", "停止", "停下", "算了")
    if not any(marker in normalized for marker in cancel_markers):
        return None
    if any(
        term in normalized
        for term in (
            "后面的不用",
            "后面的都不用",
            "后续不用",
            "取消后续",
            "取消后面的",
        )
    ):
        return IntentQueueControl(action=IntentQueueControlAction.CANCEL_PENDING)
    if any(term in normalized for term in ("全部取消", "都取消", "全部停止", "停止所有", "都不用做")):
        return IntentQueueControl(action=IntentQueueControlAction.CANCEL_ALL)
    for intent, terms in _INTENT_TERMS:
        if any(term in normalized for term in terms):
            return IntentQueueControl(
                action=IntentQueueControlAction.CANCEL_INTENT,
                target_intent=intent,
            )
    if any(term in normalized for term in ("取消当前", "停止当前", "先停下", "先停止")):
        return IntentQueueControl(action=IntentQueueControlAction.CANCEL_CURRENT)
    return None


def apply_intent_queue_control(
    plan: ConversationIntentPlan,
    control: IntentQueueControl,
    *,
    now: datetime | None = None,
) -> tuple[ConversationIntentPlan, str]:
    """Apply cancellation to a copied plan and return user-facing feedback."""
    updated = plan.model_copy(deep=True)
    cancellable = {
        IntentStepStatus.READY,
        IntentStepStatus.READY_WITH_UNDO,
        IntentStepStatus.AWAITING_CONSENT,
        IntentStepStatus.ACTIVE,
    }
    live_indexes = [
        index
        for index, step in enumerate(updated.execution_plan.steps)
        if step.status in cancellable
    ]
    targets: set[int] = set()
    if live_indexes:
        if control.action == IntentQueueControlAction.CANCEL_ALL:
            targets.update(live_indexes)
        elif control.action == IntentQueueControlAction.CANCEL_CURRENT:
            targets.add(live_indexes[0])
        elif control.action == IntentQueueControlAction.CANCEL_PENDING:
            first_live_index = live_indexes[0]
            first_live = updated.execution_plan.steps[first_live_index]
            primary_already_finished = (
                first_live_index > 0
                and first_live.relation == IntentRelation.AFTER_PRIMARY
                and first_live.status != IntentStepStatus.ACTIVE
            )
            targets.update(
                live_indexes if primary_already_finished else live_indexes[1:]
            )
        elif control.target_intent is not None:
            targets.update(
                index
                for index in live_indexes
                if updated.execution_plan.steps[index].intent
                == control.target_intent
            )
    if not targets:
        return plan, "没有找到可取消的对应任务。"
    for index in targets:
        updated.execution_plan.steps[index].status = IntentStepStatus.CANCELLED
    _refresh_plan_projection(updated)
    updated.updated_at = now or datetime.now(UTC)
    return updated, _control_response(control, len(targets))


def complete_current_intent_step(
    plan: ConversationIntentPlan,
    *,
    expected_intent: Intent,
    effect: IntentStepEffect | None = None,
    now: datetime | None = None,
) -> ConversationIntentPlan:
    """Complete the current matching step and expose the next queued step."""
    updated = plan.model_copy(deep=True)
    current = _first_live_step(updated)
    if current is None or current.intent != expected_intent:
        return plan
    if effect is not None:
        if not current.undo_supported:
            return plan
        current.effect = effect
    current.status = IntentStepStatus.COMPLETED
    _refresh_plan_projection(updated)
    updated.updated_at = now or datetime.now(UTC)
    return updated


def undo_calendar_step_effect(
    plan: ConversationIntentPlan,
    *,
    step_id: str,
    now: datetime | None = None,
) -> ConversationIntentPlan:
    """Mark one applied calendar receipt undone after verified compensation."""
    updated = plan.model_copy(deep=True)
    step = next(
        (item for item in updated.execution_plan.steps if item.step_id == step_id),
        None,
    )
    if (
        step is None
        or step.effect is None
        or step.effect.kind != "calendar_event"
        or step.effect.status != IntentEffectStatus.APPLIED
    ):
        return plan
    step.effect.status = IntentEffectStatus.UNDONE
    step.effect.undone_at = now or datetime.now(UTC)
    updated.updated_at = step.effect.undone_at
    return updated


def latest_applied_calendar_effect_step(
    plan: ConversationIntentPlan,
) -> IntentExecutionStep | None:
    """Return the latest applied calendar effect eligible for compensation."""
    return next(
        (
            step
            for step in reversed(plan.execution_plan.steps)
            if step.effect is not None
            and step.effect.kind == "calendar_event"
            and step.effect.status == IntentEffectStatus.APPLIED
        ),
        None,
    )


def activate_current_ready_step(
    plan: ConversationIntentPlan,
    *,
    expected_step_id: str,
    now: datetime | None = None,
) -> ConversationIntentPlan:
    """Mark only the first live ready step active after execution starts."""
    updated = plan.model_copy(deep=True)
    current = _first_live_step(updated)
    if (
        current is None
        or current.step_id != expected_step_id
        or current.status != IntentStepStatus.READY
    ):
        return plan
    current.status = IntentStepStatus.ACTIVE
    _refresh_plan_projection(updated)
    updated.updated_at = now or datetime.now(UTC)
    return updated


def activate_current_consented_step(
    plan: ConversationIntentPlan,
    *,
    expected_intent: Intent,
    now: datetime | None = None,
) -> ConversationIntentPlan:
    """Activate an awaiting step after the owner accepts its proposal."""
    updated = plan.model_copy(deep=True)
    current = _first_live_step(updated)
    if (
        current is None
        or current.intent != expected_intent
        or current.status != IntentStepStatus.AWAITING_CONSENT
    ):
        return plan
    current.status = IntentStepStatus.ACTIVE
    _refresh_plan_projection(updated)
    updated.updated_at = now or datetime.now(UTC)
    return updated


def _first_live_step(
    plan: ConversationIntentPlan,
) -> IntentExecutionStep | None:
    live_statuses = {
        IntentStepStatus.READY,
        IntentStepStatus.READY_WITH_UNDO,
        IntentStepStatus.AWAITING_CONSENT,
        IntentStepStatus.ACTIVE,
    }
    return next(
        (
            step
            for step in plan.execution_plan.steps
            if step.status in live_statuses
        ),
        None,
    )


def _refresh_plan_projection(plan: ConversationIntentPlan) -> None:
    """Recompute aggregate and compatibility fields from authoritative steps."""
    live_statuses = {
        IntentStepStatus.READY,
        IntentStepStatus.READY_WITH_UNDO,
        IntentStepStatus.AWAITING_CONSENT,
        IntentStepStatus.ACTIVE,
    }
    remaining = [
        step
        for step in plan.execution_plan.steps
        if step.status in live_statuses
    ]
    plan.execution_plan.pending_intents = [
        step.intent for step in remaining[1:]
    ]
    if remaining:
        plan.execution_plan.current_intent = remaining[0].intent
        plan.status = ConversationIntentPlanStatus.ACTIVE
    elif all(
        step.status == IntentStepStatus.CANCELLED
        for step in plan.execution_plan.steps
    ):
        plan.status = ConversationIntentPlanStatus.CANCELLED
    else:
        plan.status = ConversationIntentPlanStatus.COMPLETED


def _control_response(control: IntentQueueControl, count: int) -> str:
    if control.action == IntentQueueControlAction.CANCEL_PENDING:
        return f"好的，已取消后续 {count} 个任务，当前任务保持不变。"
    if control.action == IntentQueueControlAction.CANCEL_CURRENT:
        return "好的，已取消当前任务。"
    if control.action == IntentQueueControlAction.CANCEL_ALL:
        return f"好的，已取消全部 {count} 个未完成任务。"
    return "好的，已从任务队列中取消你指定的任务。"
