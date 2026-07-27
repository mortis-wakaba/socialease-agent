"""Exposure-planning domain adapter for unified conversations."""

import re

from app.conversation.adapters.base import ModuleAdapterResult
from app.models_conversation import (
    ExposureMessageEventPayload,
    ExposureParameters,
    ModuleRun,
)
from app.models_exposure import ExposurePlanRequest
from app.services.exposure_service import ExposureService, exposure_service


class ExposureModuleAdapter:
    """Start an exposure plan after the user supplies an intensity level."""

    def __init__(self, service: ExposureService | None = None) -> None:
        self._service = service or exposure_service

    async def start(self, run: ModuleRun) -> ModuleAdapterResult:
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
    ) -> ModuleAdapterResult:
        if run.domain_session_id:
            return ModuleAdapterResult(
                response=(
                    "分级练习计划已经保存在当前会话中。你可以继续讨论如何降低下一步"
                    "强度，或手动结束当前模块。"
                ),
                domain_session_id=run.domain_session_id,
                event_payload=ExposureMessageEventPayload(
                    plan_id=run.domain_session_id,
                ),
            )
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

    async def suspend(self, run: ModuleRun) -> None:
        del run

    async def resume(self, run: ModuleRun) -> None:
        del run

    async def terminate(self, run: ModuleRun) -> None:
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


def _parameters(run: ModuleRun) -> ExposureParameters:
    if not isinstance(run.module_parameters, ExposureParameters):
        raise ValueError("exposure run has invalid parameters")
    return run.module_parameters


def _extract_anxiety_level(message: str) -> int | None:
    match = re.search(r"(?<!\d)(10|[1-9])(?!\d)", message)
    return int(match.group(1)) if match else None
