"""Stateless, independently testable stages for the agent harness pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import ChatRequest, Intent, IntentResult, RiskLevel, SafetyResult
from app.safety.actions import HarnessAction
from app.safety.classifier import BaseSafetyClassifier
from app.safety.permissions import PermissionAction, SafetyPermissionGate
from app.workflow.hooks import AgentHarnessHook
from app.workflow.recovery import ErrorCategory, format_trace_error
from app.workflow.router import BaseIntentRouter


@dataclass(frozen=True)
class SafetyStageResult:
    """Safety result plus sanitized errors produced by conservative fallback."""

    safety_result: SafetyResult
    errors: tuple[str, ...] = ()


class SafetyCheckStage:
    """Run safety hooks and fail closed when classification is unavailable."""

    def __init__(
        self,
        *,
        classifier: BaseSafetyClassifier,
        hooks: tuple[AgentHarnessHook, ...],
    ) -> None:
        self._classifier = classifier
        self._hooks = hooks

    async def execute(
        self,
        request: ChatRequest,
        *,
        trusted_result: SafetyResult | None = None,
    ) -> SafetyStageResult:
        """Return trusted safety or classify with a conservative fallback."""
        for hook in self._hooks:
            method = getattr(hook, "before_safety", None)
            if method is not None:
                method(request)
        errors: tuple[str, ...] = ()
        if trusted_result is not None:
            result = trusted_result
        else:
            try:
                result = await self._classifier.classify(request.message)
            except Exception as exc:
                result = SafetyResult(
                    risk_level=RiskLevel.HIGH,
                    reason=(
                        "Safety classifier failed; using conservative high-risk "
                        "fallback instead of ordinary low-risk handling."
                    ),
                )
                errors = (
                    format_trace_error(ErrorCategory.SAFETY_CLASSIFIER_FAILURE, exc),
                )
        for hook in self._hooks:
            method = getattr(hook, "after_safety", None)
            if method is not None:
                method(request, result)
        return SafetyStageResult(safety_result=result, errors=errors)


class IntentRoutingStage:
    """Route an already classified turn while preserving crisis preemption."""

    def __init__(
        self,
        *,
        router: BaseIntentRouter,
        permission_gate: SafetyPermissionGate,
        hooks: tuple[AgentHarnessHook, ...],
    ) -> None:
        self._router = router
        self._permission_gate = permission_gate
        self._hooks = hooks

    async def execute(
        self,
        request: ChatRequest,
        safety_result: SafetyResult,
        *,
        trusted_result: IntentResult | None = None,
    ) -> IntentResult:
        """Force crisis routing before considering trusted or model routing."""
        crisis_decision = self._permission_gate.decide(
            safety_result,
            HarnessAction.CRISIS_ESCALATION,
        )
        if crisis_decision.action == PermissionAction.ESCALATE:
            result = IntentResult(
                intent=Intent.CRISIS,
                confidence=1.0,
                reason="Safety permission gate required crisis escalation.",
            )
        elif trusted_result is not None:
            result = trusted_result
        else:
            result = await self._router.route(request.message, safety_result)
        for hook in self._hooks:
            method = getattr(hook, "after_routing", None)
            if method is not None:
                method(request, result)
        return result
