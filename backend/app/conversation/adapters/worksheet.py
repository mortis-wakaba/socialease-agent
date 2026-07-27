"""Worksheet domain adapter for unified conversations."""

from app.conversation.adapters.base import ModuleAdapterResult
from app.models_conversation import (
    ModuleRun,
    WorksheetMessageEventPayload,
    WorksheetParameters,
)
from app.models_worksheet import WorksheetCreateRequest, WorksheetSupplementRequest
from app.services.worksheet_service import WorksheetService, worksheet_service


class WorksheetModuleAdapter:
    """Map a worksheet draft to a conversation module frame."""

    def __init__(self, service: WorksheetService | None = None) -> None:
        self._service = service or worksheet_service

    async def start(self, run: ModuleRun) -> ModuleAdapterResult:
        parameters = _parameters(run)
        result = await self._service.create_worksheet(
            WorksheetCreateRequest(
                user_id=run.user_id,
                message=parameters.situation,
            )
        )
        worksheet_id = (
            result.worksheet.worksheet_id if result.worksheet else None
        )
        if worksheet_id is None:
            raise ValueError("worksheet could not be started safely")
        return ModuleAdapterResult(
            response=result.response,
            domain_session_id=worksheet_id,
            event_payload=WorksheetMessageEventPayload(
                worksheet_id=worksheet_id,
                completed=result.worksheet.completed,
                missing_fields=result.missing_fields,
            ),
        )

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
    ) -> ModuleAdapterResult:
        worksheet_id = _worksheet_id(run)
        result = await self._service.supplement_worksheet(
            WorksheetSupplementRequest(
                worksheet_id=worksheet_id,
                user_id=run.user_id,
                message=message,
            )
        )
        worksheet = result.worksheet
        if worksheet is None:
            raise ValueError("worksheet update was blocked")
        return ModuleAdapterResult(
            response=result.response,
            domain_session_id=worksheet_id,
            event_payload=WorksheetMessageEventPayload(
                worksheet_id=worksheet_id,
                completed=worksheet.completed,
                missing_fields=result.missing_fields,
            ),
        )

    async def suspend(self, run: ModuleRun) -> None:
        del run

    async def resume(self, run: ModuleRun) -> None:
        del run

    async def terminate(self, run: ModuleRun) -> None:
        del run

    async def delete_runtime_context(self, run: ModuleRun) -> None:
        if run.domain_session_id:
            await self._service.draft_store.delete(
                user_id=run.user_id,
                task_id=run.domain_session_id,
            )


def _parameters(run: ModuleRun) -> WorksheetParameters:
    if not isinstance(run.module_parameters, WorksheetParameters):
        raise ValueError("worksheet run has invalid parameters")
    return run.module_parameters


def _worksheet_id(run: ModuleRun) -> str:
    if not run.domain_session_id:
        raise ValueError("worksheet domain session is unavailable")
    return run.domain_session_id
