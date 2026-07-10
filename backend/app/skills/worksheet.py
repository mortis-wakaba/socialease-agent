"""Executable worksheet skill for the lead chat harness."""

from pathlib import Path
from typing import Any

from app.models import Intent
from app.models_worksheet import WorksheetCreateRequest
from app.services.worksheet_service import WorksheetService, worksheet_service
from app.skills.base import SkillContext, SkillDescriptor, SkillResult


class WorksheetSkill:
    """Create a CBT-style self-reflection worksheet draft from chat."""

    descriptor = SkillDescriptor(
        name="worksheet_skill",
        description="Creates a non-medical self-reflection worksheet draft.",
        supported_intents=(Intent.CBT_WORKSHEET,),
        entrypoint="app.skills.worksheet.WorksheetSkill.run",
        safety_notes="Runs only after the lead harness safety gate; crisis is handled before this skill.",
        manifest_path=str(Path(__file__).parent / "manifests" / "worksheet" / "SKILL.md"),
    )

    def __init__(self, service: WorksheetService | None = None) -> None:
        self.service = service or worksheet_service

    async def run(self, context: SkillContext) -> SkillResult:
        """Create and persist a worksheet draft for the current message."""
        result = await self.service.create_worksheet(
            WorksheetCreateRequest(user_id=context.user_id, message=context.message)
        )
        worksheet_id = result.worksheet.worksheet_id if result.worksheet else None
        structured_data: dict[str, Any] = {
            "agent": "worksheet_agent",
            "action": "worksheet_created" if worksheet_id else "worksheet_blocked",
            "worksheet_id": worksheet_id,
            "missing_fields": result.missing_fields,
            "gentle_followup_questions": result.gentle_followup_questions,
            "disclaimer": result.disclaimer,
            "citations": [
                citation.model_dump(mode="json")
                for citation in (result.worksheet.citations if result.worksheet else [])
            ],
            "next_ui": "worksheet",
            "blocked": result.blocked,
        }
        return SkillResult(
            response=result.response,
            structured_data=structured_data,
            selected_agent="worksheet_agent",
        )

