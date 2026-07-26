"""Executable role-play skill for the lead chat harness."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.models_context import SkillContextProjection
from app.models_roleplay import RoleplayStartRequest
from app.services.roleplay_service import RoleplayService, roleplay_service
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


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
        scenario_description = _scenario_description_from_context(
            context.selected_context,
            context.message,
        )
        difficulty = _difficulty_from_context(context.selected_context)
        result = await self.service.start_session(
            RoleplayStartRequest(
                user_id=context.user_id,
                scenario_description=scenario_description,
                difficulty=difficulty,
            )
        )
        structured_data: dict[str, Any] = {
            "agent": "roleplay_agent",
            "action": "roleplay_started",
            "session_id": result.session.session_id,
            "scenario": result.session.scenario_spec.model_dump(mode="json")
            if result.session.scenario_spec is not None
            else None,
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


def _scenario_description_from_context(
    selected_context: SkillContextProjection | None,
    message: str,
) -> str:
    """Prefer the current request over potentially stale profile context."""
    values = selected_context.values if selected_context is not None else {}
    raw = values.get("scenario")
    if isinstance(raw, str) and raw.strip() and raw not in message:
        return f"{message.strip()}；补充场景：{raw.strip()}"[:1200]
    return message.strip()[:1200] or "练习一次具体的社交表达"


def _difficulty_from_context(selected_context: SkillContextProjection | None) -> int:
    """Return a validated selected difficulty or the conservative baseline."""
    if selected_context is None:
        return 2
    value = selected_context.values.get("preferred_difficulty")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5:
        return value
    return 2
