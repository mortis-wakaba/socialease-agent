"""Common contract for conversation module adapters."""

from dataclasses import dataclass
from typing import Protocol

from app.models_conversation import ConversationEventPayload, ModuleRun
from app.models_conversation_context import ConversationWorkingContext
from app.models_module_overlay import ModuleOverlay


@dataclass(frozen=True)
class ModuleAdapterResult:
    """Bounded result returned to the coordinator after a domain action."""

    response: str
    domain_session_id: str | None
    event_payload: ConversationEventPayload


class ModuleAdapter(Protocol):
    """Translate conversation lifecycle actions to one domain service."""

    async def start(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext,
    ) -> ModuleAdapterResult: ...
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
    async def suspend(self, run: ModuleRun) -> None: ...
    async def resume(self, run: ModuleRun) -> None: ...
    async def terminate(self, run: ModuleRun) -> None: ...
    async def delete_runtime_context(self, run: ModuleRun) -> None: ...
