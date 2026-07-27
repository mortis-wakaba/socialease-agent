"""Grounded support-resource adapter for unified conversations."""

from app.conversation.adapters.base import ModuleAdapterResult
from app.models_conversation import (
    ModuleRun,
    ResourceMessageEventPayload,
    ResourceParameters,
)
from app.models_support import SupportQueryRequest
from app.services.support_resource_service import (
    SupportResourceService,
    support_resource_service,
)


class ResourceModuleAdapter:
    """Map one bounded resource-search context to a module frame."""

    def __init__(self, service: SupportResourceService | None = None) -> None:
        self._service = service or support_resource_service

    async def start(self, run: ModuleRun) -> ModuleAdapterResult:
        parameters = _parameters(run)
        return await self._query(run, parameters.query)

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
    ) -> ModuleAdapterResult:
        return await self._query(run, message)

    async def suspend(self, run: ModuleRun) -> None:
        del run

    async def resume(self, run: ModuleRun) -> None:
        del run

    async def terminate(self, run: ModuleRun) -> None:
        del run

    async def _query(
        self,
        run: ModuleRun,
        query: str,
    ) -> ModuleAdapterResult:
        result = await self._service.query_resources(
            SupportQueryRequest(
                query=query,
                user_id=run.user_id,
                search_session_id=run.domain_session_id,
            )
        )
        if result.blocked:
            raise ValueError("resource query was blocked by safety policy")
        return ModuleAdapterResult(
            response=result.answer,
            domain_session_id=result.search_session_id,
            event_payload=ResourceMessageEventPayload(
                search_session_id=result.search_session_id,
                citation_count=len(result.citations),
                unknown=result.unknown,
            ),
        )


def _parameters(run: ModuleRun) -> ResourceParameters:
    if not isinstance(run.module_parameters, ResourceParameters):
        raise ValueError("resource run has invalid parameters")
    return run.module_parameters
