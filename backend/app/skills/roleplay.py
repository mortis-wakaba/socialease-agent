"""Executable role-play skill for the lead chat harness."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.models_memory import MemoryContext
from app.models_roleplay import RoleplayScenario, RoleplayStartRequest
from app.services.roleplay_service import RoleplayService, roleplay_service
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


SCENARIO_KEYWORDS: dict[RoleplayScenario, tuple[str, ...]] = {
    RoleplayScenario.CLASSROOM_SPEECH: ("课堂", "发言", "上课", "presentation", "speech"),
    RoleplayScenario.GROUP_DISCUSSION: ("小组", "讨论", "组会", "group"),
    RoleplayScenario.DORM_CONFLICT: ("宿舍", "室友", "寝室", "dorm"),
    RoleplayScenario.CLUB_ICEBREAKING: ("社团", "破冰", "迎新", "club"),
    RoleplayScenario.INVITE_CLASSMATE_MEAL: ("吃饭", "约同学", "邀请", "meal"),
    RoleplayScenario.ASK_TEACHER_QUESTION: ("老师", "提问", "office hour", "teacher"),
    RoleplayScenario.INTERVIEW_SELF_INTRO: ("面试", "自我介绍", "interview"),
    RoleplayScenario.REFUSE_REQUEST: ("拒绝", "不想答应", "边界", "refuse"),
    RoleplayScenario.EXPRESS_DISAGREEMENT: ("不同意见", "反对", "不同看法", "disagree"),
}


class RoleplaySkill:
    """Start a low-intensity role-play session from a chat request."""

    descriptor = SkillDescriptor(
        name="roleplay_skill",
        description="Starts a safe social scenario simulation and returns the created session.",
        supported_intents=(Intent.ROLEPLAY_PRACTICE,),
        entrypoint="app.skills.roleplay.RoleplaySkill.run",
        safety_notes="Runs only after the lead harness safety gate; crisis is handled before this skill.",
        manifest_path=str(Path(__file__).parent / "manifests" / "roleplay" / "SKILL.md"),
    )

    def __init__(self, service: RoleplayService | None = None) -> None:
        self.service = service or roleplay_service

    async def run(self, context: SkillContext) -> SkillResult:
        """Create a role-play session using context slots or conservative defaults."""
        scenario = _scenario_from_context(
            context.request_context,
            context.message,
            context.memory_context,
        )
        difficulty = _int_from_context(
            context.request_context,
            "difficulty",
            default=_default_difficulty(context.memory_context),
            minimum=1,
            maximum=5,
        )
        result = self.service.start_session(
            RoleplayStartRequest(
                user_id=context.user_id,
                scenario=scenario,
                difficulty=difficulty,
            )
        )
        structured_data: dict[str, Any] = {
            "agent": "roleplay_agent",
            "action": "roleplay_started",
            "session_id": result.session.session_id,
            "scenario": result.session.scenario.value,
            "difficulty": result.session.difficulty,
            "citations": [
                citation.model_dump(mode="json")
                for citation in result.session.retrieved_guidance.citations
            ],
            "no_guidance_found": result.session.retrieved_guidance.no_guidance_found,
            "next_ui": "practice",
            "blocked": False,
        }
        return SkillResult(
            response=result.opening_message,
            structured_data=structured_data,
            selected_agent="roleplay_agent",
        )


def _scenario_from_context(
    request_context: dict[str, Any],
    message: str,
    memory_context: MemoryContext | None,
) -> RoleplayScenario:
    raw = request_context.get("scenario")
    if isinstance(raw, str):
        try:
            return RoleplayScenario(raw)
        except ValueError:
            pass

    lowered = message.casefold()
    for scenario, keywords in SCENARIO_KEYWORDS.items():
        if any(keyword.casefold() in lowered for keyword in keywords):
            return scenario
    if memory_context is not None:
        for recent in memory_context.recent_scenarios:
            try:
                return RoleplayScenario(recent)
            except ValueError:
                recent_lowered = recent.casefold()
                for scenario, keywords in SCENARIO_KEYWORDS.items():
                    if any(keyword.casefold() in recent_lowered for keyword in keywords):
                        return scenario
    return RoleplayScenario.CLASSROOM_SPEECH


def _default_difficulty(memory_context: MemoryContext | None) -> int:
    """Prefer privacy-safe memory defaults over a generic baseline."""
    if memory_context is not None and memory_context.preferred_difficulty is not None:
        return memory_context.preferred_difficulty
    return 2


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
