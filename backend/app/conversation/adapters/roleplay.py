"""Role-play domain adapter for unified conversations."""

from datetime import UTC, datetime

from app.conversation.adapters.base import ModuleAdapterResult, PreparedModuleStart
from app.models_conversation import (
    ModuleRun,
    RoleplayMessageEventPayload,
    RoleplayParameters,
)
from app.models_conversation_context import ConversationWorkingContext
from app.models_module_overlay import (
    ModuleOverlay,
    ParentResumeProjection,
    RoleplayOverlay,
)
from app.models_roleplay import (
    RoleplayMessageRequest,
    RoleplayPauseRequest,
    RoleplayResumeRequest,
    RoleplayStartRequest,
)
from app.services.roleplay_service import RoleplayService, roleplay_service


class RoleplayModuleAdapter:
    """Map role-play sessions to a conversation module frame."""

    def __init__(self, service: RoleplayService | None = None) -> None:
        self._service = service or roleplay_service

    async def prepare_start(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext,
    ) -> PreparedModuleStart:
        del context
        parameters = _parameters(run)
        prepared = await self._service.prepare_conversation_session(
            RoleplayStartRequest(
                user_id=run.user_id,
                scenario_description=parameters.scenario_description,
                practice_goal=parameters.practice_goal,
                difficulty=parameters.difficulty,
            ),
        )
        return PreparedModuleStart(payload=prepared)

    async def persist_start(
        self,
        run: ModuleRun,
        prepared: PreparedModuleStart,
    ) -> ModuleAdapterResult:
        result = await self._service.persist_prepared_conversation_session(
            prepared.payload,
            session_id=run.domain_session_id,
        )
        return ModuleAdapterResult(
            response=result.opening_message,
            domain_session_id=result.session.session_id,
            event_payload=RoleplayMessageEventPayload(
                session_id=result.session.session_id,
            ),
        )

    async def after_start_commit(
        self,
        run: ModuleRun,
        prepared: PreparedModuleStart,
        result: ModuleAdapterResult,
    ) -> None:
        del prepared
        session_id = result.domain_session_id
        if session_id is None:
            raise ValueError("role-play session id missing after commit")
        session = await self._service.store.get_for_user(
            session_id, run.user_id
        )
        if session is None:
            raise LookupError("role-play session not found after commit")
        await self._service.after_conversation_session_commit(session)

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
        context: ConversationWorkingContext,
        overlay: ModuleOverlay,
    ) -> ModuleAdapterResult:
        if not isinstance(overlay.payload, RoleplayOverlay):
            raise ValueError("role-play overlay payload is invalid")
        session_id = _session_id(run)
        result = await self._service.send_conversation_message(
            RoleplayMessageRequest(
                session_id=session_id,
                user_id=run.user_id,
                message=message,
            ),
            context=context,
            overlay=overlay,
        )
        return ModuleAdapterResult(
            response=result.response,
            domain_session_id=session_id,
            event_payload=RoleplayMessageEventPayload(
                session_id=session_id,
                blocked=result.blocked,
            ),
        )

    async def build_overlay(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext | None = None,
    ) -> ModuleOverlay:
        """Project role-play domain state without copying its transcript."""
        del context
        session = await self._service.store.get_for_user(
            _session_id(run),
            run.user_id,
        )
        if session is None:
            raise LookupError("role-play session not found")
        scenario = session.scenario_spec
        if scenario is None:
            raise ValueError("role-play scenario is unavailable")
        payload = RoleplayOverlay(
            scenario_summary=scenario.safe_summary,
            practice_goal=scenario.practice_goal,
            difficulty=session.difficulty,
            current_role="user",
            counterpart_position=scenario.counterpart_role.value,
        )
        return ModuleOverlay(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            module_run_id=run.module_run_id,
            module_type=run.module_type,
            parent_module_run_id=run.parent_module_run_id,
            phase=session.status.value,
            payload=payload,
            version=run.version,
            updated_at=session.updated_at or datetime.now(UTC),
        )

    def project_for_parent_resume(
        self,
        overlay: ModuleOverlay,
    ) -> ParentResumeProjection:
        """Expose only the bounded point needed to resume role-play."""
        if not isinstance(overlay.payload, RoleplayOverlay):
            raise ValueError("role-play overlay payload is invalid")
        return ParentResumeProjection(
            module_type=overlay.module_type,
            module_run_id=overlay.module_run_id,
            resume_point=(
                overlay.payload.resume_point
                or overlay.payload.unresolved_question
                or overlay.payload.scenario_summary
            ),
            version=overlay.version,
        )

    async def suspend(self, run: ModuleRun) -> None:
        await self._service.pause_conversation_session(
            RoleplayPauseRequest(
                session_id=_session_id(run),
                user_id=run.user_id,
            )
        )

    async def resume(self, run: ModuleRun) -> None:
        await self._service.resume_conversation_session(
            RoleplayResumeRequest(
                session_id=_session_id(run),
                user_id=run.user_id,
            )
        )

    async def terminate(self, run: ModuleRun) -> None:
        await self._service.complete_conversation_session(
            session_id=_session_id(run),
            user_id=run.user_id,
        )

    async def delete_runtime_context(self, run: ModuleRun) -> None:
        del run


def _parameters(run: ModuleRun) -> RoleplayParameters:
    if not isinstance(run.module_parameters, RoleplayParameters):
        raise ValueError("role-play run has invalid parameters")
    return run.module_parameters


def _session_id(run: ModuleRun) -> str:
    if not run.domain_session_id:
        raise ValueError("role-play domain session is unavailable")
    return run.domain_session_id
