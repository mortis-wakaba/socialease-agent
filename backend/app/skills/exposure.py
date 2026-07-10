"""Executable exposure-planning skill for the lead chat harness."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.models_memory import MemoryContext
from app.models_exposure import ExposurePlanRequest
from app.services.exposure_service import ExposureService, exposure_service
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class ExposurePlanningSkill:
    """Create a low-intensity social practice plan from chat."""

    descriptor = SkillDescriptor(
        name="exposure_planning_skill",
        description="Creates a graded, stoppable social practice plan.",
        supported_intents=(Intent.EXPOSURE_PLANNING, Intent.PROGRESS_REVIEW),
        entrypoint="app.skills.exposure.ExposurePlanningSkill.run",
        safety_notes="Runs only after the lead harness safety gate; future consent gates should protect plan start.",
        manifest_path=str(Path(__file__).parent / "manifests" / "exposure" / "SKILL.md"),
    )

    def __init__(self, service: ExposureService | None = None) -> None:
        self.service = service or exposure_service

    async def run(self, context: SkillContext) -> SkillResult:
        """Create a graded practice plan with conservative defaults."""
        anxiety_level = _int_from_context(
            context.request_context,
            "current_anxiety_level",
            default=_default_anxiety_level(context.memory_context),
            minimum=1,
            maximum=10,
        )
        target_scenario = _target_scenario_from_context(
            context.request_context,
            context.message,
            context.memory_context,
        )
        previous_attempts = _previous_attempts_from_context(context.request_context)
        result = await self.service.create_plan(
            ExposurePlanRequest(
                user_id=context.user_id,
                target_scenario=target_scenario,
                current_anxiety_level=anxiety_level,
                previous_attempts=previous_attempts,
            )
        )
        plan_id = result.plan.plan_id if result.plan else None
        structured_data: dict[str, Any] = {
            "agent": "exposure_planner",
            "action": "exposure_plan_created" if plan_id else "exposure_plan_blocked",
            "plan_id": plan_id,
            "session_id": plan_id,
            "target_scenario": result.plan.target_scenario if result.plan else target_scenario,
            "recommended_next_task_id": result.plan.recommended_next_task_id if result.plan else None,
            "task_count": len(result.plan.tasks) if result.plan else 0,
            "preview_tasks": [
                task.model_dump(mode="json")
                for task in (result.plan.tasks[:2] if result.plan else [])
            ],
            "next_ui": "progress",
            "blocked": result.blocked,
        }
        return SkillResult(
            response=result.response,
            structured_data=structured_data,
            selected_agent="exposure_planner",
        )


def _target_scenario_from_context(
    request_context: dict[str, Any],
    message: str,
    memory_context: MemoryContext | None,
) -> str:
    raw = request_context.get("target_scenario")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if _looks_like_generic_plan_request(message) and memory_context is not None:
        for scenario in memory_context.recent_scenarios:
            if scenario.strip():
                return scenario.strip()
        if memory_context.onboarding_profile.preferred_scenario:
            return memory_context.onboarding_profile.preferred_scenario
    return message


def _looks_like_generic_plan_request(message: str) -> bool:
    """Return whether the message lacks a concrete scenario and asks for planning."""
    lowered = message.casefold()
    plan_words = ("计划", "练习", "阶梯", "plan", "practice")
    concrete_words = ("课堂", "宿舍", "小组", "社团", "老师", "面试", "拒绝", "吃饭")
    return any(word in lowered for word in plan_words) and not any(
        word in lowered for word in concrete_words
    )


def _default_anxiety_level(memory_context: MemoryContext | None) -> int:
    """Prefer recent anxiety memory over a generic baseline."""
    if memory_context is not None and memory_context.latest_anxiety_level is not None:
        return memory_context.latest_anxiety_level
    return 5


def _previous_attempts_from_context(request_context: dict[str, Any]) -> list[str]:
    raw = request_context.get("previous_attempts")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def _int_from_context(
    request_context: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = request_context.get(key, default)
    if not isinstance(value, int):
        return default
    return max(minimum, min(maximum, value))
