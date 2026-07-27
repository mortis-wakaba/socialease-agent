"""Allocate one bounded prompt context from shared conversation state."""

from __future__ import annotations

from dataclasses import dataclass
import json

from app.memory.token_estimator import TokenEstimator
from app.models_active_memory import ActiveMemoryPacket
from app.models_conversation import (
    ConversationEvent,
    ConversationEventType,
    ModuleRun,
)
from app.models_conversation_context import (
    ConversationCompactSummary,
    ConversationContextBudgets,
)


@dataclass(frozen=True)
class AllocatedConversationContext:
    """Selected prompt fields and content-free allocation diagnostics."""

    current_message: str
    recent_events: list[ConversationEvent]
    compact_summary: ConversationCompactSummary | None
    module_stack: list[ModuleRun]
    selected_memory: list[str]
    estimated_tokens: int
    dropped_sections: list[str]


class UnifiedContextTokenAllocator:
    """Apply one priority and token policy to ordinary and module contexts."""

    def __init__(self, token_estimator: TokenEstimator) -> None:
        self._token_estimator = token_estimator

    def allocate(
        self,
        *,
        current_user_message: str,
        current_event_id: str | None,
        recent_source: list[ConversationEvent],
        compact_summary: ConversationCompactSummary | None,
        module_stack: list[ModuleRun],
        active_memory: ActiveMemoryPacket | None,
        budgets: ConversationContextBudgets,
    ) -> AllocatedConversationContext:
        """Return one bounded selection where the current request wins."""
        dropped: list[str] = []
        current_message = _truncate_to_budget(
            current_user_message,
            budgets.current_request_tokens,
            self._token_estimator,
        )
        if current_message != current_user_message:
            dropped.append("current_request_truncated")

        recent_events = _select_recent_turns(
            recent_source,
            token_budget=budgets.recent_events_tokens,
            token_estimator=self._token_estimator,
            excluded_event_id=current_event_id,
        )
        eligible_recent_count = len(
            [
                event
                for event in recent_source
                if event.event_type
                not in {
                    ConversationEventType.CRISIS_INPUT,
                    ConversationEventType.CRISIS_ESCALATED,
                }
                and event.event_id != current_event_id
            ]
        )
        if len(recent_events) < eligible_recent_count:
            dropped.append("older_recent_events")

        selected_summary = _fit_summary(
            compact_summary,
            token_budget=budgets.summary_tokens,
            token_estimator=self._token_estimator,
        )
        if compact_summary is not None and selected_summary is None:
            dropped.append("compact_summary")

        selected_stack = _fit_module_stack(
            module_stack,
            token_budget=budgets.module_stack_tokens,
            token_estimator=self._token_estimator,
        )
        if len(selected_stack) < len(module_stack):
            dropped.append("suspended_module_frames")

        selected_memory = _select_agent_memory(
            active_memory,
            token_budget=budgets.active_memory_tokens,
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
        while estimated_tokens > budgets.total_tokens and recent_events:
            recent_events = _drop_oldest_turn(recent_events)
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
        if estimated_tokens > budgets.total_tokens:
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
        if estimated_tokens > budgets.total_tokens:
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

        return AllocatedConversationContext(
            current_message=current_message,
            recent_events=recent_events,
            compact_summary=selected_summary,
            module_stack=selected_stack,
            selected_memory=selected_memory,
            estimated_tokens=estimated_tokens,
            dropped_sections=dropped,
        )


def _select_recent_turns(
    events: list[ConversationEvent],
    *,
    token_budget: int,
    token_estimator: TokenEstimator,
    excluded_event_id: str | None,
) -> list[ConversationEvent]:
    eligible = [
        event
        for event in events
        if event.event_type
        not in {
            ConversationEventType.CRISIS_INPUT,
            ConversationEventType.CRISIS_ESCALATED,
        }
        and event.event_id != excluded_event_id
    ]
    groups: list[list[ConversationEvent]] = []
    for event in eligible:
        if event.role.value == "user" or not groups:
            groups.append([event])
        else:
            groups[-1].append(event)

    selected_groups: list[list[ConversationEvent]] = []
    used = 0
    for group in reversed(groups):
        cost = sum(_event_token_cost(event, token_estimator) for event in group)
        if used + cost > token_budget:
            continue
        selected_groups.append(group)
        used += cost
    return [
        event
        for group in reversed(selected_groups)
        for event in group
    ]


def _drop_oldest_turn(
    events: list[ConversationEvent],
) -> list[ConversationEvent]:
    if not events:
        return []
    next_user_index = next(
        (
            index
            for index, event in enumerate(events[1:], start=1)
            if event.role.value == "user"
        ),
        len(events),
    )
    return events[next_user_index:]


def _event_token_cost(
    event: ConversationEvent,
    token_estimator: TokenEstimator,
) -> int:
    return token_estimator.count(
        json.dumps(
            {
                "type": event.event_type.value,
                "role": event.role.value,
                "content": event.content,
            },
            ensure_ascii=False,
        )
    )


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
