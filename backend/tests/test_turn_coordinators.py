"""Unit tests for non-crisis turn coordination boundaries."""

from datetime import UTC, datetime

import pytest

from app.conversation.turn_coordinators import (
    IntentDispatchCoordinator,
    IntentQueueCoordinator,
)
from app.conversation.turns import ConversationTurn
from app.models import Intent, IntentResult, RiskLevel, SafetyResult
from app.models_conversation import (
    HISTORY_NOTICE_VERSION,
    Conversation,
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
)


class RecordingRouter:
    """Return a stable intent while recording coordinator dispatches."""

    def __init__(self, intent: Intent) -> None:
        self.intent = intent
        self.calls = 0

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        self.calls += 1
        return IntentResult(
            intent=self.intent,
            confidence=1.0,
            reason="test",
        )


async def _unexpected_callback(**values: object) -> object:
    del values
    raise AssertionError("inactive coordinator dependency was invoked")


def _turn(*, risk_level: RiskLevel, message: str = "继续聊聊") -> ConversationTurn:
    now = datetime.now(UTC)
    return ConversationTurn(
        conversation=Conversation(
            conversation_id="conversation-1",
            user_id="user-1",
            title="test",
            created_at=now,
            updated_at=now,
            history_notice_version=HISTORY_NOTICE_VERSION,
        ),
        conversation_id="conversation-1",
        user_id="user-1",
        message=message,
        idempotency_key="command-1",
        stack=[],
        safety_result=SafetyResult(risk_level=risk_level, reason="test"),
        user_event=ConversationEvent(
            event_id="event-1",
            conversation_id="conversation-1",
            user_id="user-1",
            sequence_no=1,
            event_type=ConversationEventType.USER_MESSAGE,
            role=ConversationEventRole.USER,
            content=message,
            idempotency_key="user:command-1",
            created_at=now,
        ),
        intent_plan=None,
        streaming_requested=False,
        response_chunk_sink=None,
    )


@pytest.mark.anyio
async def test_queue_coordinator_abstains_without_queue_state_or_control() -> None:
    coordinator = IntentQueueCoordinator(
        repository=object(),  # type: ignore[arg-type]
        safety_classifier=object(),  # type: ignore[arg-type]
        context_manager=object(),  # type: ignore[arg-type]
        module_coordinator=object(),  # type: ignore[arg-type]
        reversible_calendar_executor=object(),  # type: ignore[arg-type]
        consume_ready_steps=_unexpected_callback,  # type: ignore[arg-type]
    )

    assert await coordinator.handle(_turn(risk_level=RiskLevel.LOW)) is None


@pytest.mark.anyio
async def test_dispatch_coordinator_returns_general_intent_to_fallback() -> None:
    router = RecordingRouter(Intent.EMOTIONAL_SUPPORT)
    coordinator = IntentDispatchCoordinator(
        repository=object(),  # type: ignore[arg-type]
        route_intent=router.route,
        context_manager=object(),  # type: ignore[arg-type]
        module_coordinator=object(),  # type: ignore[arg-type]
        consume_ready_steps=_unexpected_callback,  # type: ignore[arg-type]
        create_proposal=_unexpected_callback,  # type: ignore[arg-type]
    )

    result = await coordinator.handle(_turn(risk_level=RiskLevel.LOW))

    assert result.intent_result.intent is Intent.EMOTIONAL_SUPPORT
    assert result.resolution is None
    assert router.calls == 1


@pytest.mark.anyio
async def test_dispatch_coordinator_blocks_module_proposal_above_low_risk() -> None:
    router = RecordingRouter(Intent.ROLEPLAY_PRACTICE)
    coordinator = IntentDispatchCoordinator(
        repository=object(),  # type: ignore[arg-type]
        route_intent=router.route,
        context_manager=object(),  # type: ignore[arg-type]
        module_coordinator=object(),  # type: ignore[arg-type]
        consume_ready_steps=_unexpected_callback,  # type: ignore[arg-type]
        create_proposal=_unexpected_callback,  # type: ignore[arg-type]
    )

    result = await coordinator.handle(_turn(risk_level=RiskLevel.HIGH))

    assert result.intent_result.intent is Intent.ROLEPLAY_PRACTICE
    assert result.resolution is None
    assert router.calls == 1
