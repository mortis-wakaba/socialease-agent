"""Unit tests for independently executable harness stages."""

import pytest

from app.models import ChatRequest, Intent, IntentResult, RiskLevel, SafetyResult
from app.safety.permissions import SafetyPermissionGate
from app.workflow.stages import IntentRoutingStage, SafetyCheckStage


class RecordingStageHook:
    """Record only the lifecycle callbacks owned by these stages."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def before_safety(self, request: ChatRequest) -> None:
        del request
        self.events.append("before_safety")

    def after_safety(self, request: ChatRequest, result: SafetyResult) -> None:
        del request
        self.events.append(f"after_safety:{result.risk_level.value}")

    def after_routing(self, request: ChatRequest, result: IntentResult) -> None:
        del request
        self.events.append(f"after_routing:{result.intent.value}")


class FailingClassifier:
    async def classify(self, message: str) -> SafetyResult:
        del message
        raise RuntimeError("private provider details")


class CountingRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def route(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        del message, safety_result
        self.calls += 1
        return IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=1.0,
            reason="test",
        )


@pytest.mark.anyio
async def test_safety_stage_fails_closed_and_preserves_hook_order() -> None:
    hook = RecordingStageHook()
    stage = SafetyCheckStage(
        classifier=FailingClassifier(),  # type: ignore[arg-type]
        hooks=(hook,),  # type: ignore[arg-type]
    )

    result = await stage.execute(ChatRequest(user_id="u", message="hello"))

    assert result.safety_result.risk_level is RiskLevel.HIGH
    assert result.errors[0].startswith("SAFETY_CLASSIFIER_FAILURE:")
    assert "private provider details" not in result.errors[0]
    assert hook.events == ["before_safety", "after_safety:high"]


@pytest.mark.anyio
async def test_routing_stage_cannot_bypass_crisis_with_trusted_intent() -> None:
    hook = RecordingStageHook()
    router = CountingRouter()
    stage = IntentRoutingStage(
        router=router,
        permission_gate=SafetyPermissionGate(),
        hooks=(hook,),  # type: ignore[arg-type]
    )

    result = await stage.execute(
        ChatRequest(user_id="u", message="test"),
        SafetyResult(risk_level=RiskLevel.CRISIS, reason="test"),
        trusted_result=IntentResult(
            intent=Intent.EMOTIONAL_SUPPORT,
            confidence=1.0,
            reason="must not win",
        ),
    )

    assert result.intent is Intent.CRISIS
    assert router.calls == 0
    assert hook.events == ["after_routing:crisis"]
