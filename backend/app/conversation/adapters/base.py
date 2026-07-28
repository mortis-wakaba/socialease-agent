"""Common contract for conversation module adapters."""

from dataclasses import dataclass
from typing import Any, Protocol

from app.models_conversation import ConversationEventPayload, ModuleRun
from app.models_conversation_context import ConversationWorkingContext
from app.models_module_overlay import ModuleOverlay, ParentResumeProjection


@dataclass(frozen=True)
class ModuleAdapterResult:
    """Bounded result returned to the coordinator after a domain action."""

    response: str
    domain_session_id: str | None
    event_payload: ConversationEventPayload


@dataclass(frozen=True)
class PreparedModuleStart:
    """Transaction-free computation needed to persist one module session."""

    payload: Any


class ModuleAdapter(Protocol):
    """Translate conversation lifecycle actions to one domain service."""

    async def prepare_start(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext,
    ) -> PreparedModuleStart: ...
    async def persist_start(
        self,
        run: ModuleRun,
        prepared: PreparedModuleStart,
    ) -> ModuleAdapterResult: ...
    async def after_start_commit(
        self,
        run: ModuleRun,
        prepared: PreparedModuleStart,
        result: ModuleAdapterResult,
    ) -> None: ...
    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
        context: ConversationWorkingContext,
        overlay: ModuleOverlay,
    ) -> ModuleAdapterResult: ...
    async def build_overlay(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext | None = None,
    ) -> ModuleOverlay: ...
    def project_for_parent_resume(
        self,
        overlay: ModuleOverlay,
    ) -> ParentResumeProjection: ...
    async def suspend(self, run: ModuleRun) -> None: ...
    async def resume(self, run: ModuleRun) -> None: ...
    async def terminate(self, run: ModuleRun) -> None: ...
    async def delete_runtime_context(self, run: ModuleRun) -> None: ...
