"""Typed state passed between unified-conversation turn handlers."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import SafetyResult
from app.models_conversation import Conversation, ConversationEvent, ModuleRun
from app.models_intent_plan import ConversationIntentPlan
from app.workflow.context import ResponseChunkSink


@dataclass
class ConversationTurn:
    """Application-owned state for one claimed conversation command."""

    conversation: Conversation
    conversation_id: str
    user_id: str
    message: str
    idempotency_key: str
    stack: list[ModuleRun]
    safety_result: SafetyResult
    user_event: ConversationEvent
    intent_plan: ConversationIntentPlan | None
    streaming_requested: bool
    response_chunk_sink: ResponseChunkSink | None

    @property
    def active_run(self) -> ModuleRun | None:
        """Return the top module frame, if the conversation is in a module."""
        return self.stack[-1] if self.stack else None
