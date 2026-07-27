"""Grounded support-resource adapter for unified conversations."""

from datetime import UTC, datetime

from app.conversation.adapters.base import ModuleAdapterResult
from app.models_conversation import (
    ConversationEventRole,
    ModuleRun,
    ResourceMessageEventPayload,
    ResourceParameters,
)
from app.models_conversation_context import ConversationWorkingContext
from app.models_module_overlay import ModuleOverlay, ResourceOverlay
from app.models_support import SupportQueryRequest
from app.services.support_resource_service import (
    SupportResourceService,
    support_resource_service,
)


class ResourceModuleAdapter:
    """Map one bounded resource-search context to a module frame."""

    def __init__(self, service: SupportResourceService | None = None) -> None:
        self._service = service or support_resource_service

    async def start(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext,
    ) -> ModuleAdapterResult:
        del context
        parameters = _parameters(run)
        return await self._query(run, parameters.query)

    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
        context: ConversationWorkingContext,
        overlay: ModuleOverlay,
    ) -> ModuleAdapterResult:
        if not isinstance(overlay.payload, ResourceOverlay):
            raise ValueError("resource overlay payload is invalid")
        return await self._query(run, message)

    async def build_overlay(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext | None = None,
    ) -> ModuleOverlay:
        """Rebuild grounded citation references from bounded search state."""
        session_id = _session_id(run)
        state = await self._service.get_search_context(
            user_id=run.user_id,
            session_id=session_id,
        )
        if state is None:
            citation_ids, unknown = (
                _latest_resource_projection(context, run)
                if context is not None
                else ([], True)
            )
            state = await self._service.rebuild_search_context(
                user_id=run.user_id,
                session_id=session_id,
                citation_ids=citation_ids,
                retrieval_unknown=unknown,
            )
        payload = ResourceOverlay(
            search_session_id=session_id,
            query_scope="support_resources",
            ordered_citation_ids=state.ordered_citation_ids,
            selected_citation_index=state.selected_citation_index,
            retrieval_unknown=state.retrieval_unknown,
            awaiting_user_choice=bool(state.ordered_citation_ids),
        )
        return ModuleOverlay(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            module_run_id=run.module_run_id,
            module_type=run.module_type,
            parent_module_run_id=run.parent_module_run_id,
            phase="unknown" if payload.retrieval_unknown else "results",
            payload=payload,
            version=run.version,
            updated_at=state.updated_at or datetime.now(UTC),
        )

    async def suspend(self, run: ModuleRun) -> None:
        del run

    async def resume(self, run: ModuleRun) -> None:
        del run

    async def terminate(self, run: ModuleRun) -> None:
        del run

    async def delete_runtime_context(self, run: ModuleRun) -> None:
        if run.domain_session_id:
            await self._service.search_store.delete(
                user_id=run.user_id,
                task_id=run.domain_session_id,
            )

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
                citation_ids=[
                    citation.citation_id
                    for citation in result.citations
                    if citation.citation_id is not None
                ],
                unknown=result.unknown,
            ),
        )


def _parameters(run: ModuleRun) -> ResourceParameters:
    if not isinstance(run.module_parameters, ResourceParameters):
        raise ValueError("resource run has invalid parameters")
    return run.module_parameters


def _session_id(run: ModuleRun) -> str:
    if not run.domain_session_id:
        raise ValueError("resource search session is unavailable")
    return run.domain_session_id


def _latest_resource_projection(
    context: ConversationWorkingContext,
    run: ModuleRun,
) -> tuple[list[str], bool]:
    for event in reversed(context.recent_events):
        payload = event.structured_payload
        if (
            event.module_run_id == run.module_run_id
            and event.role is ConversationEventRole.ASSISTANT
            and isinstance(payload, ResourceMessageEventPayload)
        ):
            return payload.citation_ids, payload.unknown
    return [], True
