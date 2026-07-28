"""Assemble bounded shared context from durable conversation projections."""

from __future__ import annotations

from app.conversation.compactor import ConversationCompactor
from app.conversation.context_allocator import UnifiedContextTokenAllocator
from app.conversation.context_provider import (
    ConversationContextProvider,
    DatabaseConversationContextProvider,
)
from app.conversation.repository import (
    ConversationConcurrencyError,
    ConversationRepository,
    _encode_event_cursor,
)
from app.memory.token_estimator import ConservativeTokenEstimator, TokenEstimator
from app.models_active_memory import ActiveMemoryPacket
from app.models_conversation import ConversationEvent, ModuleRun
from app.models_conversation_context import (
    ConversationCompactSummary,
    ConversationContextBudgets,
    ConversationContextDiagnostics,
    ConversationContextProfile,
    ConversationWorkingContext,
    context_budgets_for_profile,
    conversation_id_hash,
)


class ConversationContextManager:
    """Build prompt context without loading an unbounded timeline."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        compactor: ConversationCompactor,
        token_estimator: TokenEstimator | None = None,
        budgets: ConversationContextBudgets | None = None,
        provider: ConversationContextProvider | None = None,
        allocator: UnifiedContextTokenAllocator | None = None,
        recent_window_size: int = 32,
        compact_batch_size: int = 100,
    ) -> None:
        self._repository = repository
        self._compactor = compactor
        self._token_estimator = token_estimator or ConservativeTokenEstimator()
        self._fixed_budgets = budgets
        self._provider = provider or DatabaseConversationContextProvider(
            repository
        )
        self._allocator = allocator or UnifiedContextTokenAllocator(
            self._token_estimator
        )
        self._recent_window_size = min(max(recent_window_size, 8), 64)
        self._compact_batch_size = min(max(compact_batch_size, 16), 200)

    async def assemble(
        self,
        *,
        conversation_id: str,
        user_id: str,
        current_user_message: str,
        current_event_id: str | None = None,
        active_memory: ActiveMemoryPacket | None = None,
    ) -> ConversationWorkingContext:
        """Return a bounded context where the current request has precedence."""
        snapshot = await self._provider.load(
            conversation_id=conversation_id,
            user_id=user_id,
            recent_limit=self._recent_window_size,
        )
        if snapshot.conversation is None:
            raise LookupError("conversation not found")

        summary = await self._compact_older_events(
            conversation_id=conversation_id,
            user_id=user_id,
            recent_source=snapshot.recent_events,
            previous=snapshot.compact_summary,
        )
        profile = _profile_for_stack(snapshot.module_stack)
        budgets = self._fixed_budgets or context_budgets_for_profile(profile)
        allocated = self._allocator.allocate(
            current_user_message=current_user_message,
            current_event_id=current_event_id,
            recent_source=snapshot.recent_events,
            compact_summary=summary,
            module_stack=snapshot.module_stack,
            active_memory=active_memory,
            budgets=budgets,
        )

        diagnostics = ConversationContextDiagnostics(
            conversation_id_hash=conversation_id_hash(conversation_id),
            recent_event_count=len(allocated.recent_events),
            recent_event_sequence_start=(
                allocated.recent_events[0].sequence_no
                if allocated.recent_events
                else None
            ),
            recent_event_sequence_end=(
                allocated.recent_events[-1].sequence_no
                if allocated.recent_events
                else None
            ),
            compact_summary_version=(
                allocated.compact_summary.version
                if allocated.compact_summary
                else None
            ),
            active_module_count=len(allocated.module_stack),
            selected_memory_count=len(allocated.selected_memory),
            estimated_tokens=allocated.estimated_tokens,
            total_token_budget=budgets.total_tokens,
            budget_profile=profile,
            dropped_sections=allocated.dropped_sections,
            tokenizer_backend=self._token_estimator.backend_name,
            context_backend=self._provider.backend_name,
            cache_status=snapshot.cache_status,
        )
        return ConversationWorkingContext(
            conversation_id=conversation_id,
            current_user_message=allocated.current_message,
            recent_events=allocated.recent_events,
            compact_summary=allocated.compact_summary,
            active_module_stack=allocated.module_stack,
            selected_agent_memory=allocated.selected_memory,
            diagnostics=diagnostics,
        )

    async def invalidate(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Invalidate one cached projection after deletion or same-version writes."""
        await self._provider.invalidate(
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def delete_user_cache(self, *, user_id: str) -> int:
        """Delete every cached projection when supported by the provider."""
        delete_user = getattr(self._provider, "delete_user", None)
        if delete_user is None:
            return 0
        return int(await delete_user(user_id=user_id))

    async def close(self) -> None:
        """Close the context provider's optional Redis client."""
        await self._provider.close()

    async def health(self) -> bool:
        """Return whether the configured context provider is ready."""
        return await self._provider.health()

    async def _compact_older_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_source: list[ConversationEvent],
        previous: ConversationCompactSummary | None,
    ) -> ConversationCompactSummary | None:
        if len(recent_source) < self._recent_window_size:
            return previous
        compact_before = recent_source[0].sequence_no
        compacted_through = (
            previous.compacted_through_sequence if previous else 0
        )
        if compacted_through >= compact_before - 1:
            return previous
        page = await self._repository.list_events(
            conversation_id=conversation_id,
            user_id=user_id,
            cursor=(
                _encode_event_cursor(compacted_through)
                if compacted_through
                else None
            ),
            limit=self._compact_batch_size,
        )
        candidates = [
            event
            for event in page.items
            if event.sequence_no < compact_before
        ]
        if not candidates:
            return previous
        summary = await self._compactor.compact(
            conversation_id=conversation_id,
            user_id=user_id,
            previous=previous,
            events=candidates,
        )
        try:
            saved = await self._repository.save_compact_summary(
                summary,
                expected_version=previous.version if previous else None,
            )
            await self._provider.invalidate(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            return saved
        except ConversationConcurrencyError:
            return await self._repository.get_compact_summary(
                conversation_id=conversation_id,
                user_id=user_id,
            )


def _profile_for_stack(
    stack: list[ModuleRun],
) -> ConversationContextProfile:
    if not stack:
        return ConversationContextProfile.ORDINARY
    return ConversationContextProfile(stack[-1].module_type.value)
