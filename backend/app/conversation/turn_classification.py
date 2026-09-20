"""Concurrent, side-effect-free safety and intent classification for one turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

from app.models import IntentResult, RiskLevel, SafetyResult
from app.observability.turn_latency import mark_turn_flag
from app.workflow.router import reconcile_intent_risk_policy


IntentRoute = Callable[[str, SafetyResult], Awaitable[IntentResult]]
SafetyClassify = Callable[[str], Awaitable[SafetyResult]]


@dataclass(frozen=True)
class TurnClassification:
    """Final safety result and an optional non-crisis intent result."""

    safety_result: SafetyResult
    intent_result: IntentResult | None


class ConcurrentTurnClassifier:
    """Speculatively route while safety classification is in flight."""

    def __init__(
        self,
        *,
        classify_safety: SafetyClassify,
        route_intent: IntentRoute,
    ) -> None:
        self._classify_safety = classify_safety
        self._route_intent = route_intent

    async def classify(self, message: str) -> TurnClassification:
        """Run both classifiers concurrently and let safety own final authority."""
        speculative_safety = SafetyResult(
            risk_level=RiskLevel.LOW,
            reason="Speculative routing placeholder; never persisted.",
        )
        routing_task = asyncio.create_task(
            self._route_intent(message, speculative_safety)
        )
        try:
            safety_result = await self._classify_safety(message)
        except BaseException:
            await _cancel_and_discard(routing_task)
            raise

        if safety_result.risk_level is RiskLevel.CRISIS:
            await _cancel_and_discard(routing_task)
            mark_turn_flag("routing_speculation_cancelled")
            return TurnClassification(
                safety_result=safety_result,
                intent_result=None,
            )

        intent_result = await routing_task
        return TurnClassification(
            safety_result=safety_result,
            intent_result=reconcile_intent_risk_policy(
                intent_result,
                risk_level=safety_result.risk_level,
            ),
        )


async def _cancel_and_discard(task: asyncio.Task[IntentResult]) -> None:
    """Consume a speculative task even when cancellation loses a completion race."""
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task
