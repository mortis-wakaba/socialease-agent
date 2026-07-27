"""Assemble bounded shared context from durable conversation projections."""

from __future__ import annotations

import json

from app.conversation.compactor import ConversationCompactor
from app.conversation.repository import (
    ConversationConcurrencyError,
    ConversationRepository,
    _encode_event_cursor,
)
from app.memory.token_estimator import ConservativeTokenEstimator, TokenEstimator
from app.models_active_memory import ActiveMemoryPacket
from app.models_conversation import (
    ConversationEvent,
    ConversationEventType,
    ModuleRun,
)
from app.models_conversation_context import (
    ConversationCompactSummary,
    ConversationContextBudgets,
    ConversationContextDiagnostics,
    ConversationWorkingContext,
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
        recent_window_size: int = 32,
        compact_batch_size: int = 100,
    ) -> None:
        self._repository = repository
        self._compactor = compactor
        self._token_estimator = token_estimator or ConservativeTokenEstimator()
        self._budgets = budgets or ConversationContextBudgets()
        self._recent_window_size = min(max(recent_window_size, 8), 64)
        self._compact_batch_size = min(max(compact_batch_size, 16), 200)

    async def assemble(
        self,
        *,
        conversation_id: str,
        user_id: str,
        current_user_message: str,
        active_memory: ActiveMemoryPacket | None = None,
    ) -> ConversationWorkingContext:
        """Return a bounded context where the current request has precedence."""
        conversation = self._repository.get_for_user(conversation_id, user_id)
        if conversation is None:
            raise LookupError("conversation not found")

        recent_source = self._repository.list_recent_events(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=self._recent_window_size,
        )
        summary = await self._compact_older_events(
            conversation_id=conversation_id,
            user_id=user_id,
            recent_source=recent_source,
        )
        module_stack = self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )

        dropped: list[str] = []
        current_message = _truncate_to_budget(
            current_user_message,
            self._budgets.current_request_tokens,
            self._token_estimator,
        )
        if current_message != current_user_message:
            dropped.append("current_request_truncated")

        recent_events = _select_recent_events(
            recent_source,
            token_budget=self._budgets.recent_events_tokens,
            token_estimator=self._token_estimator,
        )
        if len(recent_events) < len(
            [
                event
                for event in recent_source
                if event.event_type != ConversationEventType.CRISIS_ESCALATED
            ]
        ):
            dropped.append("older_recent_events")

        selected_summary = _fit_summary(
            summary,
            token_budget=self._budgets.summary_tokens,
            token_estimator=self._token_estimator,
        )
        if summary is not None and selected_summary is None:
            dropped.append("compact_summary")

        selected_stack = _fit_module_stack(
            module_stack,
            token_budget=self._budgets.module_stack_tokens,
            token_estimator=self._token_estimator,
        )
        if len(selected_stack) < len(module_stack):
            dropped.append("suspended_module_frames")

        selected_memory = _select_agent_memory(
            active_memory,
            token_budget=self._budgets.active_memory_tokens,
            token_estimator=self._token_estimator,
        )
        available_memory_count = (
            len(active_memory.episodic_memories) if active_memory else 0
        )
        if len(selected_memory) < available_memory_count:
            dropped.append("agent_memory")

        estimated_tokens = _context_token_count(
            current_message=current_message,
            events=recent_events,
            summary=selected_summary,
            module_stack=selected_stack,
            selected_memory=selected_memory,
            token_estimator=self._token_estimator,
        )
        while (
            estimated_tokens > self._budgets.total_tokens
            and recent_events
        ):
            recent_events.pop(0)
            if "older_recent_events" not in dropped:
                dropped.append("older_recent_events")
            estimated_tokens = _context_token_count(
                current_message=current_message,
                events=recent_events,
                summary=selected_summary,
                module_stack=selected_stack,
                selected_memory=selected_memory,
                token_estimator=self._token_estimator,
            )
        if estimated_tokens > self._budgets.total_tokens:
            selected_memory = []
            if "agent_memory" not in dropped:
                dropped.append("agent_memory")
            estimated_tokens = _context_token_count(
                current_message=current_message,
                events=recent_events,
                summary=selected_summary,
                module_stack=selected_stack,
                selected_memory=selected_memory,
                token_estimator=self._token_estimator,
            )
        if estimated_tokens > self._budgets.total_tokens:
            selected_summary = None
            if "compact_summary" not in dropped:
                dropped.append("compact_summary")
            estimated_tokens = _context_token_count(
                current_message=current_message,
                events=recent_events,
                summary=selected_summary,
                module_stack=selected_stack,
                selected_memory=selected_memory,
                token_estimator=self._token_estimator,
            )

        diagnostics = ConversationContextDiagnostics(
            conversation_id_hash=conversation_id_hash(conversation_id),
            recent_event_count=len(recent_events),
            recent_event_sequence_start=(
                recent_events[0].sequence_no if recent_events else None
            ),
            recent_event_sequence_end=(
                recent_events[-1].sequence_no if recent_events else None
            ),
            compact_summary_version=(
                selected_summary.version if selected_summary else None
            ),
            active_module_count=len(selected_stack),
            selected_memory_count=len(selected_memory),
            estimated_tokens=estimated_tokens,
            total_token_budget=self._budgets.total_tokens,
            dropped_sections=dropped,
            tokenizer_backend=self._token_estimator.backend_name,
        )
        return ConversationWorkingContext(
            conversation_id=conversation_id,
            current_user_message=current_message,
            recent_events=recent_events,
            compact_summary=selected_summary,
            active_module_stack=selected_stack,
            selected_agent_memory=selected_memory,
            diagnostics=diagnostics,
        )

    async def _compact_older_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        recent_source: list[ConversationEvent],
    ) -> ConversationCompactSummary | None:
        previous = self._repository.get_compact_summary(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if len(recent_source) < self._recent_window_size:
            return previous
        compact_before = recent_source[0].sequence_no
        compacted_through = (
            previous.compacted_through_sequence if previous else 0
        )
        if compacted_through >= compact_before - 1:
            return previous
        page = self._repository.list_events(
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
            return self._repository.save_compact_summary(
                summary,
                expected_version=previous.version if previous else None,
            )
        except ConversationConcurrencyError:
            return self._repository.get_compact_summary(
                conversation_id=conversation_id,
                user_id=user_id,
            )


def _select_recent_events(
    events: list[ConversationEvent],
    *,
    token_budget: int,
    token_estimator: TokenEstimator,
) -> list[ConversationEvent]:
    selected: list[ConversationEvent] = []
    used = 0
    for event in reversed(events):
        if event.event_type == ConversationEventType.CRISIS_ESCALATED:
            continue
        cost = token_estimator.count(
            json.dumps(
                {
                    "type": event.event_type.value,
                    "role": event.role.value,
                    "content": event.content,
                },
                ensure_ascii=False,
            )
        )
        if used + cost > token_budget:
            continue
        selected.append(event)
        used += cost
    return list(reversed(selected))


def _fit_summary(
    summary: ConversationCompactSummary | None,
    *,
    token_budget: int,
    token_estimator: TokenEstimator,
) -> ConversationCompactSummary | None:
    if summary is None:
        return None
    cost = token_estimator.count(
        summary.model_dump_json(
            exclude={
                "conversation_id",
                "user_id",
                "updated_at",
            }
        )
    )
    return summary if cost <= token_budget else None


def _fit_module_stack(
    stack: list[ModuleRun],
    *,
    token_budget: int,
    token_estimator: TokenEstimator,
) -> list[ModuleRun]:
    selected: list[ModuleRun] = []
    used = 0
    for run in reversed(stack):
        cost = token_estimator.count(
            json.dumps(
                {
                    "module_type": run.module_type.value,
                    "status": run.status.value,
                    "depth": run.depth,
                    "domain_session_id": run.domain_session_id,
                },
                ensure_ascii=False,
            )
        )
        if used + cost <= token_budget:
            selected.append(run)
            used += cost
    return list(reversed(selected))


def _select_agent_memory(
    packet: ActiveMemoryPacket | None,
    *,
    token_budget: int,
    token_estimator: TokenEstimator,
) -> list[str]:
    if packet is None or token_budget <= 0:
        return []
    candidates = [
        json.dumps(
            packet.stable_memory.values,
            ensure_ascii=False,
            default=str,
        ),
        *packet.episodic_memories,
    ]
    selected: list[str] = []
    used = 0
    for value in candidates:
        if not value or value == "{}":
            continue
        cost = token_estimator.count(value)
        if used + cost <= token_budget:
            selected.append(value)
            used += cost
    return selected


def _truncate_to_budget(
    value: str,
    token_budget: int,
    token_estimator: TokenEstimator,
) -> str:
    if token_estimator.count(value) <= token_budget:
        return value
    low, high = 1, len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        if token_estimator.count(value[:midpoint]) <= token_budget:
            low = midpoint
        else:
            high = midpoint - 1
    return value[:low]


def _context_token_count(
    *,
    current_message: str,
    events: list[ConversationEvent],
    summary: ConversationCompactSummary | None,
    module_stack: list[ModuleRun],
    selected_memory: list[str],
    token_estimator: TokenEstimator,
) -> int:
    payload = {
        "current_user_message": current_message,
        "recent_events": [
            {
                "type": event.event_type.value,
                "role": event.role.value,
                "content": event.content,
            }
            for event in events
        ],
        "compact_summary": (
            summary.model_dump(
                mode="json",
                exclude={"conversation_id", "user_id", "updated_at"},
            )
            if summary
            else None
        ),
        "active_module_stack": [
            {
                "module_type": run.module_type.value,
                "status": run.status.value,
                "depth": run.depth,
            }
            for run in module_stack
        ],
        "selected_agent_memory": selected_memory,
    }
    return token_estimator.count(json.dumps(payload, ensure_ascii=False))
