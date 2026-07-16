"""Executable role-play skill for the lead chat harness."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.models_context import SkillContextProjection
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
            context.selected_context,
            context.message,
        )
        difficulty = _difficulty_from_context(context.selected_context)
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
    selected_context: SkillContextProjection | None,
    message: str,
) -> RoleplayScenario:
    values = selected_context.values if selected_context is not None else {}
    raw = values.get("scenario")
    if isinstance(raw, str):
        try:
            return RoleplayScenario(raw)
        except ValueError:
            pass

    lowered = message.casefold()
    for scenario, keywords in SCENARIO_KEYWORDS.items():
        if any(keyword.casefold() in lowered for keyword in keywords):
            return scenario
    recent_scenarios = values.get("recent_scenarios")
    if isinstance(recent_scenarios, list):
        for recent in recent_scenarios:
            if not isinstance(recent, str):
                continue
            try:
                return RoleplayScenario(recent)
            except ValueError:
                recent_lowered = recent.casefold()
                for scenario, keywords in SCENARIO_KEYWORDS.items():
                    if any(keyword.casefold() in recent_lowered for keyword in keywords):
                        return scenario
    return RoleplayScenario.CLASSROOM_SPEECH


def _difficulty_from_context(selected_context: SkillContextProjection | None) -> int:
    """Return a validated selected difficulty or the conservative baseline."""
    if selected_context is None:
        return 2
    value = selected_context.values.get("preferred_difficulty")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5:
        return value
    return 2
