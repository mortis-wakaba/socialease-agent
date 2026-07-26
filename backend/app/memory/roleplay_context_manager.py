"""Build bounded role-play prompts from Redis session context."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math

from app.memory.roleplay_compactor import RoleplayCompactor
from app.memory.session_context_settings import RoleplaySessionContextSettings
from app.memory.session_context_store import (
    SessionContextStore,
    SessionContextStoreUnavailable,
)
from app.memory.token_estimator import TokenEstimator, create_token_estimator
from app.models_roleplay import RoleplayMessageRole
from app.models_session_context import (
    DurableCheckpointContext,
    RoleplayCompactState,
    RoleplayContextDiagnostics,
    RoleplayPromptContext,
    RoleplaySessionContext,
    SessionContextMessage,
)


class RoleplayContextManager:
    """Coordinate TTL context writes, compaction, and prompt budgeting."""

    def __init__(
        self,
        *,
        store: SessionContextStore,
        settings: RoleplaySessionContextSettings,
        compactor: RoleplayCompactor,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.compactor = compactor
        self.token_estimator = token_estimator or create_token_estimator(
            backend=settings.tokenizer_backend,
            model_name=settings.tokenizer_model,
        )

    async def initialize(
        self,
        *,
        user_id: str,
        session_id: str,
        opening_message: str,
    ) -> bool:
        try:
            await self.store.initialize(
                user_id=user_id,
                session_id=session_id,
                opening_message=opening_message,
                ttl_seconds=self.settings.active_ttl_seconds,
            )
            return True
        except SessionContextStoreUnavailable:
            return False

    async def append(
        self,
        *,
        user_id: str,
        session_id: str,
        role: RoleplayMessageRole,
        content: str,
    ) -> bool:
        try:
            await self.store.append_message(
                user_id=user_id,
                session_id=session_id,
                role=role,
                content=content,
                ttl_seconds=self.settings.active_ttl_seconds,
            )
            return True
        except SessionContextStoreUnavailable:
            return False

    async def build_prompt_context(
        self,
        *,
        user_id: str,
        session_id: str,
        scenario: str,
        difficulty: int,
        guidance: str,
        current_user_message: str,
        fallback_recent_messages: list[str],
        durable_checkpoint: DurableCheckpointContext | None = None,
    ) -> RoleplayPromptContext:
        """Return compact state and recent messages within the configured budget."""
        try:
            context = await self.store.get(user_id=user_id, session_id=session_id)
        except SessionContextStoreUnavailable:
            return self._fallback_context(
                fallback_recent_messages,
                error_category="SESSION_CONTEXT_UNAVAILABLE",
                durable_checkpoint=durable_checkpoint,
            )
        if context is None:
            return self._fallback_context(
                fallback_recent_messages,
                error_category="SESSION_CONTEXT_EXPIRED",
                durable_checkpoint=durable_checkpoint,
            )

        effective_compact_state = context.compact_state or (
            durable_checkpoint.compact_state
            if durable_checkpoint is not None
            else None
        )
        compaction_triggered = self._should_compact(
            context=context,
            scenario=scenario,
            difficulty=difficulty,
            guidance=guidance,
            current_user_message=current_user_message,
            compact_state=effective_compact_state,
        )
        compacted_count = 0
        if compaction_triggered:
            compacted_count = max(
                0,
                len(context.messages) - self.settings.recent_target_messages,
            )
            if compacted_count > 0:
                old_messages = context.messages[:compacted_count]
                compacted_through = (
                    context.compact_state.compacted_through_message
                    if context.compact_state is not None
                    else 0
                ) + compacted_count
                compact_state = await self.compactor.compact(
                    previous=context.compact_state,
                    messages=old_messages,
                    compacted_through_message=compacted_through,
                )
                candidate = context.model_copy(
                    update={
                        "messages": context.messages[compacted_count:],
                        "compact_state": compact_state,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    deep=True,
                )
                try:
                    replaced = await self.store.replace(
                        context=candidate,
                        expected_version=context.version,
                        ttl_seconds=self.settings.active_ttl_seconds,
                    )
                except SessionContextStoreUnavailable:
                    return self._fallback_context(
                        fallback_recent_messages,
                        error_category="SESSION_CONTEXT_UNAVAILABLE_DURING_COMPACTION",
                        durable_checkpoint=durable_checkpoint,
                    )
                if replaced:
                    candidate.version = context.version + 1
                    context = candidate
                    effective_compact_state = candidate.compact_state
                else:
                    try:
                        latest = await self.store.get(
                            user_id=user_id,
                            session_id=session_id,
                        )
                    except SessionContextStoreUnavailable:
                        latest = None
                    if latest is not None:
                        context = latest
                        effective_compact_state = latest.compact_state or (
                            durable_checkpoint.compact_state
                            if durable_checkpoint is not None
                            else None
                        )
                    compacted_count = 0

        history_messages = list(context.messages)
        if (
            history_messages
            and history_messages[-1].role == RoleplayMessageRole.USER
            and history_messages[-1].content == current_user_message
        ):
            history_messages = history_messages[:-1]
        recent, estimated_tokens = self._select_recent_messages(
            context=context,
            history_messages=history_messages,
            scenario=scenario,
            difficulty=difficulty,
            guidance=guidance,
            current_user_message=current_user_message,
            compact_state=effective_compact_state,
        )
        budget = self.settings.max_input_tokens
        return RoleplayPromptContext(
            recent_messages=recent,
            compact_state=effective_compact_state,
            diagnostics=RoleplayContextDiagnostics(
                backend=self.store.backend_name,
                available=True,
                recent_message_count=len(recent),
                compact_state_used=effective_compact_state is not None,
                compaction_triggered=compaction_triggered,
                compacted_message_count=compacted_count,
                durable_checkpoint_used=(
                    durable_checkpoint is not None
                    and context.compact_state is None
                    and effective_compact_state is not None
                ),
                durable_checkpoint_version=(
                    durable_checkpoint.checkpoint_version
                    if durable_checkpoint is not None
                    and context.compact_state is None
                    else None
                ),
                active_memory_estimated_tokens=(
                    durable_checkpoint.estimated_tokens
                    if durable_checkpoint is not None
                    and context.compact_state is None
                    else 0
                ),
                active_memory_token_budget=(
                    durable_checkpoint.token_budget
                    if durable_checkpoint is not None
                    and context.compact_state is None
                    else 0
                ),
                estimated_input_tokens=estimated_tokens,
                input_token_budget=budget,
                budget_utilization=min(1.0, estimated_tokens / budget),
                token_estimator_backend=self.token_estimator.backend_name,
                token_estimator_model=self.token_estimator.model_name,
            ),
        )

    async def pause(self, *, user_id: str, session_id: str) -> bool:
        try:
            return await self.store.refresh_ttl(
                user_id=user_id,
                session_id=session_id,
                ttl_seconds=self.settings.paused_ttl_seconds,
            )
        except SessionContextStoreUnavailable:
            return False

    async def resume(self, *, user_id: str, session_id: str) -> bool:
        try:
            return await self.store.refresh_ttl(
                user_id=user_id,
                session_id=session_id,
                ttl_seconds=self.settings.active_ttl_seconds,
            )
        except SessionContextStoreUnavailable:
            return False

    async def delete(self, *, user_id: str, session_id: str) -> None:
        await self.store.delete(user_id=user_id, session_id=session_id)

    async def delete_user(self, *, user_id: str) -> int:
        return await self.store.delete_user(user_id=user_id)

    async def ping(self) -> bool:
        return await self.store.ping()

    async def close(self) -> None:
        await self.store.close()

    def _should_compact(
        self,
        *,
        context: RoleplaySessionContext,
        scenario: str,
        difficulty: int,
        guidance: str,
        current_user_message: str,
        compact_state: RoleplayCompactState | None,
    ) -> bool:
        if len(context.messages) > self.settings.recent_max_messages:
            return True
        all_messages = "\n".join(
            f"{message.role.value}: {message.content}" for message in context.messages
        )
        estimated = self.token_estimator.count(
            _fixed_prompt_text(scenario, difficulty, guidance, current_user_message)
            + all_messages
            + _compact_text(compact_state)
        )
        return estimated >= math.floor(
            self.settings.max_input_tokens * self.settings.compact_trigger_ratio
        )

    def _select_recent_messages(
        self,
        *,
        context: RoleplaySessionContext,
        history_messages: list[SessionContextMessage],
        scenario: str,
        difficulty: int,
        guidance: str,
        current_user_message: str,
        compact_state: RoleplayCompactState | None,
    ) -> tuple[list[str], int]:
        fixed_tokens = self.token_estimator.count(
            _fixed_prompt_text(scenario, difficulty, guidance, current_user_message)
            + _compact_text(compact_state)
        )
        remaining = max(1, self.settings.max_input_tokens - fixed_tokens)
        selected_reversed: list[str] = []
        used = 0
        for message in reversed(history_messages[-self.settings.recent_max_messages :]):
            rendered = f"{message.role.value}: {message.content}"
            cost = self.token_estimator.count(rendered)
            if selected_reversed and used + cost > remaining:
                break
            if not selected_reversed and cost > remaining:
                rendered = _truncate_to_token_budget(
                    rendered,
                    remaining,
                    estimator=self.token_estimator,
                )
                cost = self.token_estimator.count(rendered)
            selected_reversed.append(rendered)
            used += cost
        selected = list(reversed(selected_reversed))
        return selected, min(self.settings.max_input_tokens, fixed_tokens + used)

    def _fallback_context(
        self,
        recent_messages: list[str],
        *,
        error_category: str,
        durable_checkpoint: DurableCheckpointContext | None = None,
    ) -> RoleplayPromptContext:
        compact_state = (
            durable_checkpoint.compact_state
            if durable_checkpoint is not None
            else None
        )
        compact_tokens = self.token_estimator.count(_compact_text(compact_state))
        remaining = max(1, self.settings.max_input_tokens - compact_tokens)
        selected_reversed: list[str] = []
        used = 0
        for message in reversed(
            recent_messages[-self.settings.recent_max_messages :]
        ):
            cost = self.token_estimator.count(message)
            if selected_reversed and used + cost > remaining:
                break
            rendered = message
            if not selected_reversed and cost > remaining:
                rendered = _truncate_to_token_budget(
                    message,
                    remaining,
                    estimator=self.token_estimator,
                )
                cost = self.token_estimator.count(rendered)
            selected_reversed.append(rendered)
            used += cost
        bounded = list(reversed(selected_reversed))
        estimated = min(
            self.settings.max_input_tokens,
            compact_tokens + used,
        )
        return RoleplayPromptContext(
            recent_messages=bounded,
            compact_state=compact_state,
            diagnostics=RoleplayContextDiagnostics(
                backend=self.store.backend_name,
                available=False,
                fallback_used=True,
                recent_message_count=len(bounded),
                compact_state_used=compact_state is not None,
                durable_checkpoint_used=durable_checkpoint is not None,
                durable_checkpoint_version=(
                    durable_checkpoint.checkpoint_version
                    if durable_checkpoint is not None
                    else None
                ),
                active_memory_estimated_tokens=(
                    durable_checkpoint.estimated_tokens
                    if durable_checkpoint is not None
                    else 0
                ),
                active_memory_token_budget=(
                    durable_checkpoint.token_budget
                    if durable_checkpoint is not None
                    else 0
                ),
                estimated_input_tokens=estimated,
                input_token_budget=self.settings.max_input_tokens,
                budget_utilization=min(
                    1.0,
                    estimated / self.settings.max_input_tokens,
                ),
                token_estimator_backend=self.token_estimator.backend_name,
                token_estimator_model=self.token_estimator.model_name,
                error_category=error_category,
            ),
        )


def _fixed_prompt_text(
    scenario: str,
    difficulty: int,
    guidance: str,
    current_user_message: str,
) -> str:
    return (
        f"scenario:{scenario}\ndifficulty:{difficulty}\nguidance:{guidance}\n"
        f"current:{current_user_message}\n"
        + ("x" * 1200)
    )


def _compact_text(compact_state: RoleplayCompactState | None) -> str:
    if compact_state is None:
        return ""
    return json.dumps(
        compact_state.model_dump(mode="json"),
        ensure_ascii=False,
    )


def _truncate_to_token_budget(
    text: str,
    budget: int,
    *,
    estimator: TokenEstimator,
) -> str:
    if estimator.count(text) <= budget:
        return text
    prefix = "[earlier content truncated] "
    content_budget = max(1, budget - estimator.count(prefix))
    low, high = 1, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimator.count(text[-middle:]) <= content_budget:
            low = middle
        else:
            high = middle - 1
    return prefix + text[-low:]
