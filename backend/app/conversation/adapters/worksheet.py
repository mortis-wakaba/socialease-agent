"""Worksheet domain adapter for unified conversations."""

from datetime import UTC, datetime

from app.conversation.adapters.base import ModuleAdapterResult, PreparedModuleStart
from app.models_conversation import (
    ModuleRun,
    WorksheetMessageEventPayload,
    WorksheetParameters,
)
from app.models_conversation_context import (
    ConversationPromptContext,
    ConversationPromptEvent,
    ConversationWorkingContext,
)
from app.models_module_overlay import (
    ModuleOverlay,
    ParentResumeProjection,
    WorksheetOverlay,
)
from app.models_worksheet import WorksheetCreateRequest, WorksheetSupplementRequest
from app.services.worksheet_service import WorksheetService, worksheet_service


class WorksheetModuleAdapter:
    """Map a worksheet draft to a conversation module frame."""

    def __init__(self, service: WorksheetService | None = None) -> None:
        self._service = service or worksheet_service

    async def prepare_start(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext,
    ) -> PreparedModuleStart:
        parameters = _parameters(run)
        prepared = await self._service.prepare_worksheet(
            WorksheetCreateRequest(
                user_id=run.user_id,
                message=parameters.situation,
                source_event_id=run.source_event_id,
            ),
            conversation_context=_prompt_context(context),
        )
        if prepared.blocked:
            raise ValueError("worksheet could not be started safely")
        return PreparedModuleStart(payload=prepared)

    async def persist_start(
        self,
        run: ModuleRun,
        prepared: PreparedModuleStart,
    ) -> ModuleAdapterResult:
        result = await self._service.persist_prepared_worksheet(
            prepared.payload,
            worksheet_id=run.domain_session_id,
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

    async def after_start_commit(
        self,
        run: ModuleRun,
        prepared: PreparedModuleStart,
        result: ModuleAdapterResult,
    ) -> None:
        del prepared
        worksheet_id = result.domain_session_id
        if worksheet_id is None:
            raise ValueError("worksheet id missing after commit")
        worksheet = await self._service.store.get_for_user(
            worksheet_id, run.user_id
        )
        if worksheet is None:
            raise LookupError("worksheet not found after commit")
        await self._service.after_worksheet_commit(worksheet)

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
        context: ConversationWorkingContext,
        overlay: ModuleOverlay,
    ) -> ModuleAdapterResult:
        if not isinstance(overlay.payload, WorksheetOverlay):
            raise ValueError("worksheet overlay payload is invalid")
        worksheet_id = _worksheet_id(run)
        result = await self._service.supplement_worksheet(
            WorksheetSupplementRequest(
                worksheet_id=worksheet_id,
                user_id=run.user_id,
                message=message,
            ),
            conversation_context=_prompt_context(context),
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

    async def build_overlay(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext | None = None,
    ) -> ModuleOverlay:
        """Rebuild worksheet progress from its durable domain record."""
        del context
        worksheet = await self._service.store.get_for_user(
            _worksheet_id(run),
            run.user_id,
        )
        if worksheet is None:
            raise LookupError("worksheet not found")
        fields = worksheet.fields.model_dump(mode="python")
        completed_fields = [
            name for name, value in fields.items() if value not in (None, "")
        ]
        return ModuleOverlay(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            module_run_id=run.module_run_id,
            module_type=run.module_type,
            parent_module_run_id=run.parent_module_run_id,
            phase="completed" if worksheet.completed else "collecting",
            payload=WorksheetOverlay(
                worksheet_id=worksheet.worksheet_id,
                current_section=(
                    worksheet.missing_fields[0]
                    if worksheet.missing_fields
                    else None
                ),
                completed_fields=completed_fields,
                missing_fields=worksheet.missing_fields,
                last_confirmed_field=(
                    completed_fields[-1] if completed_fields else None
                ),
            ),
            version=run.version,
            updated_at=worksheet.updated_at or datetime.now(UTC),
        )

    def project_for_parent_resume(
        self,
        overlay: ModuleOverlay,
    ) -> ParentResumeProjection:
        """Expose the next worksheet field without copying draft values."""
        if not isinstance(overlay.payload, WorksheetOverlay):
            raise ValueError("worksheet overlay payload is invalid")
        return ParentResumeProjection(
            module_type=overlay.module_type,
            module_run_id=overlay.module_run_id,
            resume_point=overlay.payload.current_section,
            version=overlay.version,
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


def _prompt_context(
    context: ConversationWorkingContext,
) -> ConversationPromptContext:
    return ConversationPromptContext(
        recent_events=[
            ConversationPromptEvent(
                event_type=event.event_type,
                role=event.role,
                content=event.content,
            )
            for event in context.recent_events
        ],
        compact_summary=context.compact_summary,
        active_module_overlay=context.active_module_overlay,
        parent_resume_projections=context.parent_resume_projections,
        retrieved_memories=context.selected_agent_memory[:3],
    )
