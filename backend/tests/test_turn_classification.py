"""Tests for speculative safety and intent classification coordination."""

import asyncio

import pytest

from app.conversation.turn_classification import ConcurrentTurnClassifier
from app.models import Intent, IntentResult, RiskLevel, SafetyResult


@pytest.mark.anyio
async def test_safety_and_intent_start_concurrently() -> None:
    safety_started = asyncio.Event()
    routing_started = asyncio.Event()
    release = asyncio.Event()

    async def classify_safety(_message: str) -> SafetyResult:
        safety_started.set()
        await release.wait()
        return SafetyResult(risk_level=RiskLevel.LOW, reason="test")

    async def route_intent(
        _message: str,
        _safety: SafetyResult,
    ) -> IntentResult:
        routing_started.set()
        await release.wait()
        return IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=1.0,
            reason="test",
        )

    task = asyncio.create_task(
        ConcurrentTurnClassifier(
            classify_safety=classify_safety,
            route_intent=route_intent,
        ).classify("普通消息")
    )
    await safety_started.wait()
    await routing_started.wait()

    assert not task.done()
    release.set()
    result = await task

    assert result.safety_result.risk_level is RiskLevel.LOW
    assert result.intent_result is not None
    assert result.intent_result.intent is Intent.EMOTIONAL_SUPPORT


@pytest.mark.anyio
async def test_crisis_cancels_and_discards_speculative_routing() -> None:
    routing_started = asyncio.Event()
    routing_cancelled = asyncio.Event()

    async def classify_safety(_message: str) -> SafetyResult:
        await routing_started.wait()
        return SafetyResult(risk_level=RiskLevel.CRISIS, reason="test")

    async def route_intent(
        _message: str,
        _safety: SafetyResult,
    ) -> IntentResult:
        routing_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            routing_cancelled.set()
        raise AssertionError("cancelled route must not produce a result")

    result = await ConcurrentTurnClassifier(
        classify_safety=classify_safety,
        route_intent=route_intent,
    ).classify("危机消息")

    assert result.safety_result.risk_level is RiskLevel.CRISIS
    assert result.intent_result is None
    assert routing_cancelled.is_set()
