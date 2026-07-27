"""Exposure-planning domain adapter for unified conversations."""

from datetime import UTC, datetime
import re

from app.conversation.adapters.base import ModuleAdapterResult
from app.models_conversation import (
    ExposureMessageEventPayload,
    ExposureParameters,
    ModuleRun,
)
from app.models_conversation_context import ConversationWorkingContext
from app.models_module_overlay import (
    ExposureOverlay,
    ModuleOverlay,
    ParentResumeProjection,
)
from app.models_exposure import (
    ExposureCompleteRequest,
    ExposureFeedbackStatus,
    ExposurePlanRequest,
)
from app.services.exposure_service import ExposureService, exposure_service


class ExposureModuleAdapter:
    """Start an exposure plan after the user supplies an intensity level."""

    def __init__(self, service: ExposureService | None = None) -> None:
        self._service = service or exposure_service

    async def start(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext,
    ) -> ModuleAdapterResult:
        del context
        parameters = _parameters(run)
        if parameters.starting_anxiety is None:
            return ModuleAdapterResult(
                response=(
                    "已进入分级社交练习。开始制定安全小步骤前，请告诉我当前压力强度"
                    "（1–10）；你可以随时暂停或结束模块。"
                ),
                domain_session_id=None,
                event_payload=ExposureMessageEventPayload(
                    awaiting_anxiety_level=True,
                ),
            )
        return await self._create_plan(run, parameters.starting_anxiety)

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
        context: ConversationWorkingContext,
        overlay: ModuleOverlay,
    ) -> ModuleAdapterResult:
        del context
        if not isinstance(overlay.payload, ExposureOverlay):
            raise ValueError("exposure overlay payload is invalid")
        if run.domain_session_id:
            return await self._handle_plan_feedback(run, message, overlay.payload)
        anxiety = _extract_anxiety_level(message)
        if anxiety is None:
            return ModuleAdapterResult(
                response="请提供 1–10 的当前压力强度；这不是诊断，只用于控制练习强度。",
                domain_session_id=None,
                event_payload=ExposureMessageEventPayload(
                    awaiting_anxiety_level=True,
                ),
            )
        return await self._create_plan(run, anxiety)

    async def build_overlay(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext | None = None,
    ) -> ModuleOverlay:
        """Rebuild explicit exposure progress without inferring user consent."""
        del context
        plan = None
        if run.domain_session_id:
            plan = self._service.store.get_by_id_for_user(
                plan_id=run.domain_session_id,
                user_id=run.user_id,
            )
            if plan is None:
                raise LookupError("exposure plan not found")
        current_task = None
        current_index = None
        completed_ids: list[str] = []
        if plan is not None:
            completed_ids = [
                attempt.task_id
                for attempt in plan.attempts
                if attempt.status.value == "completed"
            ]
            target_id = plan.recommended_next_task_id
            for index, task in enumerate(plan.tasks):
                if task.task_id == target_id or (
                    target_id is None and task.task_id not in completed_ids
                ):
                    current_task = task
                    current_index = index
                    break
        payload = ExposureOverlay(
            plan_id=plan.plan_id if plan else None,
            current_step_id=current_task.task_id if current_task else None,
            current_step_index=current_index,
            current_step_summary=current_task.title if current_task else None,
            current_intensity=(
                current_task.difficulty
                if current_task
                else (
                    plan.current_anxiety_level
                    if plan
                    else _parameters(run).starting_anxiety
                )
            ),
            attempt_status="ready" if plan else "awaiting_rating",
            last_user_rating=(
                plan.attempts[-1].anxiety_after
                if plan and plan.attempts
                else (plan.current_anxiety_level if plan else None)
            ),
            completed_step_ids=completed_ids,
            next_decision="start" if plan else "collect_rating",
        )
        return ModuleOverlay(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            module_run_id=run.module_run_id,
            module_type=run.module_type,
            parent_module_run_id=run.parent_module_run_id,
            phase=payload.attempt_status,
            payload=payload,
            version=run.version,
            updated_at=plan.updated_at if plan else datetime.now(UTC),
        )

    def project_for_parent_resume(
        self,
        overlay: ModuleOverlay,
    ) -> ParentResumeProjection:
        """Expose the bounded practice step needed after a child module."""
        if not isinstance(overlay.payload, ExposureOverlay):
            raise ValueError("exposure overlay payload is invalid")
        return ParentResumeProjection(
            module_type=overlay.module_type,
            module_run_id=overlay.module_run_id,
            resume_point=(
                overlay.payload.current_step_summary
                or overlay.payload.next_decision
            ),
            version=overlay.version,
        )

    async def suspend(self, run: ModuleRun) -> None:
        del run

    async def resume(self, run: ModuleRun) -> None:
        del run

    async def terminate(self, run: ModuleRun) -> None:
        del run

    async def delete_runtime_context(self, run: ModuleRun) -> None:
        del run

    async def _create_plan(
        self,
        run: ModuleRun,
        anxiety: int,
    ) -> ModuleAdapterResult:
        parameters = _parameters(run)
        result = await self._service.create_plan(
            ExposurePlanRequest(
                user_id=run.user_id,
                target_scenario=parameters.goal,
                current_anxiety_level=anxiety,
                previous_attempts=[],
            )
        )
        if result.blocked or result.plan is None:
            raise ValueError("exposure plan could not be started safely")
        return ModuleAdapterResult(
            response=result.response,
            domain_session_id=result.plan.plan_id,
            event_payload=ExposureMessageEventPayload(
                plan_id=result.plan.plan_id,
            ),
        )

    async def _handle_plan_feedback(
        self,
        run: ModuleRun,
        message: str,
        overlay: ExposureOverlay,
    ) -> ModuleAdapterResult:
        """Record only explicit attempt feedback; never infer progression."""
        status = _extract_feedback_status(message)
        if status is None:
            return ModuleAdapterResult(
                response=(
                    "计划已保存在当前对话。完成一次后，请明确告诉我“完成了”“跳过”"
                    "或“太难”，并提供练习前后两个 1–10 的压力强度；只有你明确说"
                    "“可以增加难度”时，下一步才可能提高强度。"
                ),
                domain_session_id=run.domain_session_id,
                event_payload=ExposureMessageEventPayload(
                    plan_id=run.domain_session_id,
                ),
            )
        ratings = _extract_ratings(message)
        if len(ratings) < 2:
            return ModuleAdapterResult(
                response="请再提供练习前和练习后的两个 1–10 压力强度，例如“完成了，前 6，后 4”。",
                domain_session_id=run.domain_session_id,
                event_payload=ExposureMessageEventPayload(
                    plan_id=run.domain_session_id,
                ),
            )
        if overlay.current_step_id is None:
            raise ValueError("exposure current step is unavailable")
        permission = _allows_increase(message)
        result = await self._service.complete_task(
            ExposureCompleteRequest(
                user_id=run.user_id,
                task_id=overlay.current_step_id,
                status=status,
                anxiety_before=ratings[0],
                anxiety_after=ratings[1],
                reflection=message,
                permission_to_increase=permission,
            )
        )
        next_title = result.next_task.title if result.next_task else None
        response = result.response
        if next_title:
            response = f"{response} 当前建议下一步：{next_title}。"
        if not permission:
            response += " 未收到增加难度的明确许可，因此不会自动提高强度。"
        return ModuleAdapterResult(
            response=response,
            domain_session_id=run.domain_session_id,
            event_payload=ExposureMessageEventPayload(
                plan_id=run.domain_session_id,
                blocked=result.blocked,
                attempt_status=status.value,
                next_task_id=(
                    result.next_task.task_id if result.next_task else None
                ),
                permission_to_increase=permission,
            ),
        )


def _parameters(run: ModuleRun) -> ExposureParameters:
    if not isinstance(run.module_parameters, ExposureParameters):
        raise ValueError("exposure run has invalid parameters")
    return run.module_parameters


def _extract_anxiety_level(message: str) -> int | None:
    match = re.search(r"(?<!\d)(10|[1-9])(?!\d)", message)
    return int(match.group(1)) if match else None


def _extract_feedback_status(
    message: str,
) -> ExposureFeedbackStatus | None:
    if re.search(r"太难|承受不了|too[ _-]?hard", message, re.IGNORECASE):
        return ExposureFeedbackStatus.TOO_HARD
    if re.search(r"跳过|没做|未做|skip", message, re.IGNORECASE):
        return ExposureFeedbackStatus.SKIPPED
    if re.search(r"完成|做了|试过|completed", message, re.IGNORECASE):
        return ExposureFeedbackStatus.COMPLETED
    return None


def _extract_ratings(message: str) -> list[int]:
    return [
        int(match)
        for match in re.findall(r"(?<!\d)(10|[1-9])(?!\d)", message)
    ][:2]


def _allows_increase(message: str) -> bool:
    return bool(
        re.search(
            r"可以(?:再)?增加难度|同意(?:再)?增加难度|可以更难|increase difficulty",
            message,
            re.IGNORECASE,
        )
    )
