"""Build one policy-scoped active-memory packet for a selected skill."""

from __future__ import annotations

import logging

from app.db.repositories import UserProfileRepository
from app.memory.active_memory_assembler import (
    ActiveMemoryAssembler,
    episodic_types_for_skill,
)
from app.memory.context_builder import build_memory_context
from app.memory.context_selector import select_skill_context
from app.memory.retriever import EpisodicMemoryRetriever
from app.memory.settings_store import UserMemorySettingsRepository
from app.models_active_memory import ActiveMemoryPacket
from app.models_long_term_memory import (
    MemoryRetrievalRequest,
    MemoryRetrievalStrategy,
    SocialSkillCode,
)
from app.models_session_context import DurableCheckpointContext


logger = logging.getLogger(__name__)


class MemoryContextOrchestrator:
    """Load stable context, retrieve episodic memory, and apply one assembler."""

    def __init__(
        self,
        *,
        user_profile_repository: UserProfileRepository,
        settings_repository: UserMemorySettingsRepository,
        episodic_retriever: EpisodicMemoryRetriever,
        assembler: ActiveMemoryAssembler | None = None,
    ) -> None:
        self.user_profile_repository = user_profile_repository
        self.settings_repository = settings_repository
        self.episodic_retriever = episodic_retriever
        self.assembler = assembler or ActiveMemoryAssembler()

    async def assemble(
        self,
        *,
        user_id: str,
        skill_name: str,
        current_request: str,
        request_context: dict[str, object] | None = None,
        scenario_type: str | None = None,
        scenario_id: str | None = None,
        practice_thread_id: str | None = None,
        skill_codes: list[SocialSkillCode] | None = None,
        durable_checkpoint: DurableCheckpointContext | None = None,
    ) -> ActiveMemoryPacket:
        """Return stable-only memory when scoped episodic retrieval is unavailable."""
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
        allowed_types = episodic_types_for_skill(skill_name)
        retrieval = None
        if allowed_types:
            try:
                retrieval = await self.episodic_retriever.retrieve(
                    MemoryRetrievalRequest(
                        user_id=user_id,
                        query=current_request,
                        allowed_memory_types=list(allowed_types),
                        scenario_type=scenario_type,
                        scenario_id=scenario_id,
                        practice_thread_id=practice_thread_id,
                        skill_codes=list(skill_codes or []),
                        strategy=MemoryRetrievalStrategy.SQL_TEXT,
                    )
                )
            except Exception as error:
                logger.warning(
                    "Episodic memory retrieval failed: %s",
                    error.__class__.__name__,
                )
        return self.assembler.assemble(
            user_id=user_id,
            skill_context=stable_context,
            current_request=current_request,
            durable_checkpoint=durable_checkpoint,
            memory_retrieval=retrieval,
            retrieval_user_id=user_id if retrieval is not None else None,
            consent_allowed=settings.consent_state.consent_to_practice_summary,
        )
