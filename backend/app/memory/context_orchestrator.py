"""Build stable and exact working memory for a selected skill."""

from __future__ import annotations

from app.db.repositories import UserProfileRepository
from app.memory.active_memory_assembler import ActiveMemoryAssembler
from app.memory.context_builder import build_memory_context
from app.memory.context_selector import select_skill_context
from app.memory.settings_store import UserMemorySettingsRepository
from app.models_active_memory import ActiveMemoryPacket
from app.models_session_context import DurableCheckpointContext


class MemoryContextOrchestrator:
    """Load stable context and exact application-owned working state."""

    def __init__(
        self,
        *,
        user_profile_repository: UserProfileRepository,
        settings_repository: UserMemorySettingsRepository,
        assembler: ActiveMemoryAssembler | None = None,
    ) -> None:
        self.user_profile_repository = user_profile_repository
        self.settings_repository = settings_repository
        self.assembler = assembler or ActiveMemoryAssembler()

    async def assemble(
        self,
        *,
        user_id: str,
        skill_name: str,
        current_request: str,
        request_context: dict[str, object] | None = None,
        durable_checkpoint: DurableCheckpointContext | None = None,
    ) -> ActiveMemoryPacket:
        """Return stable memory plus an exact checkpoint supplied by the application."""
        settings = await self.settings_repository.get(user_id)
        stable_context = select_skill_context(
            skill_name=skill_name,
            request_context=dict(request_context or {}),
            memory_context=build_memory_context(
                practice_summary=(
                    await self.user_profile_repository.get_summary(user_id)
                ),
                memory_settings=settings,
            ),
        )
        return self.assembler.assemble(
            user_id=user_id,
            skill_context=stable_context,
            current_request=current_request,
            durable_checkpoint=durable_checkpoint,
            memory_retrieval=None,
            retrieval_user_id=None,
            consent_allowed=settings.consent_state.consent_to_practice_summary,
        )
