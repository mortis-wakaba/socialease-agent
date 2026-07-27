"""Role-play domain adapter for unified conversations."""

from app.conversation.adapters.base import ModuleAdapterResult
from app.models_conversation import (
    ModuleRun,
    RoleplayMessageEventPayload,
    RoleplayParameters,
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

    async def start(self, run: ModuleRun) -> ModuleAdapterResult:
        parameters = _parameters(run)
        result = await self._service.start_session(
            RoleplayStartRequest(
                user_id=run.user_id,
                scenario_description=parameters.scenario_description,
                practice_goal=parameters.practice_goal,
                difficulty=parameters.difficulty,
            )
        )
        return ModuleAdapterResult(
            response=result.opening_message,
            domain_session_id=result.session.session_id,
            event_payload=RoleplayMessageEventPayload(
                session_id=result.session.session_id,
            ),
        )

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
    ) -> ModuleAdapterResult:
        session_id = _session_id(run)
        result = await self._service.send_message(
            RoleplayMessageRequest(
                session_id=session_id,
                user_id=run.user_id,
                message=message,
            )
        )
        return ModuleAdapterResult(
            response=result.response,
            domain_session_id=session_id,
            event_payload=RoleplayMessageEventPayload(
                session_id=session_id,
                blocked=result.blocked,
            ),
        )

    async def suspend(self, run: ModuleRun) -> None:
        await self._service.pause_session(
            RoleplayPauseRequest(
                session_id=_session_id(run),
                user_id=run.user_id,
            )
        )

    async def resume(self, run: ModuleRun) -> None:
        await self._service.resume_session(
            RoleplayResumeRequest(
                session_id=_session_id(run),
                user_id=run.user_id,
            )
        )

    async def terminate(self, run: ModuleRun) -> None:
        await self.suspend(run)

    async def delete_runtime_context(self, run: ModuleRun) -> None:
        if run.domain_session_id:
            await self._service.context_manager.delete(
                user_id=run.user_id,
                session_id=run.domain_session_id,
            )


def _parameters(run: ModuleRun) -> RoleplayParameters:
    if not isinstance(run.module_parameters, RoleplayParameters):
        raise ValueError("role-play run has invalid parameters")
    return run.module_parameters


def _session_id(run: ModuleRun) -> str:
    if not run.domain_session_id:
        raise ValueError("role-play domain session is unavailable")
    return run.domain_session_id
