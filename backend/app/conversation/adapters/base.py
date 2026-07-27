"""Common contract for conversation module adapters."""

from dataclasses import dataclass
from typing import Protocol

from app.models_conversation import ConversationEventPayload, ModuleRun


@dataclass(frozen=True)
class ModuleAdapterResult:
    """Bounded result returned to the coordinator after a domain action."""

    response: str
    domain_session_id: str | None
    event_payload: ConversationEventPayload


class ModuleAdapter(Protocol):
    """Translate conversation lifecycle actions to one domain service."""

    async def start(self, run: ModuleRun) -> ModuleAdapterResult: ...
    async def handle_message(
        self,
        run: ModuleRun,
        message: str,
    ) -> ModuleAdapterResult: ...
    async def suspend(self, run: ModuleRun) -> None: ...
    async def resume(self, run: ModuleRun) -> None: ...
    async def terminate(self, run: ModuleRun) -> None: ...
    async def delete_runtime_context(self, run: ModuleRun) -> None: ...
