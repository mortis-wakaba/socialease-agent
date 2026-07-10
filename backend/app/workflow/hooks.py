"""Lightweight lifecycle hooks around the SocialEase agent harness loop."""

from dataclasses import dataclass
from typing import Protocol

from app.models import ChatRequest, IntentResult, SafetyResult, TraceRecord
from app.safety.actions import HarnessAction
from app.safety.permissions import PermissionDecision
from app.skills import SkillResult
from app.workflow.context import RunContext


@dataclass(frozen=True)
class HookDecision:
    """Optional hook decision for action or memory-write checkpoints."""

    allow: bool = True
    reason: str = ""
    structured_data: dict[str, object] | None = None


class AgentHarnessHook(Protocol):
    """Optional observer hook for harness lifecycle events."""

    def before_safety(self, request: ChatRequest) -> None:
        """Observe a request before safety classification."""
        ...

    def after_safety(self, request: ChatRequest, safety_result: SafetyResult) -> None:
        """Observe the safety result."""
        ...

    def after_routing(self, request: ChatRequest, intent_result: IntentResult) -> None:
        """Observe the intent routing result."""
        ...

    def before_action(
        self,
        run_context: RunContext,
        harness_action: HarnessAction,
        permission_decision: PermissionDecision,
    ) -> HookDecision | None:
        """Observe or block an action before skill execution."""
        ...

    def after_action(
        self,
        run_context: RunContext,
        harness_action: HarnessAction,
        skill_result: SkillResult,
    ) -> None:
        """Observe an action after skill execution or harness-level handling."""
        ...

    def after_skill(self, request: ChatRequest, skill_result: SkillResult) -> None:
        """Observe the selected skill result before trace persistence."""
        ...

    def before_memory_write(
        self,
        run_context: RunContext,
        memory_kind: str,
        payload: dict[str, object],
    ) -> HookDecision | None:
        """Observe or block a memory write."""
        ...

    def after_trace(self, trace: TraceRecord) -> None:
        """Observe the final persisted trace record."""
        ...

    def on_stop(self, run_context: RunContext, trace: TraceRecord) -> None:
        """Observe the end of one harness run."""
        ...


class NullHarnessHook:
    """No-op hook used as a safe default extension point."""

    def before_safety(self, request: ChatRequest) -> None:
        pass

    def after_safety(self, request: ChatRequest, safety_result: SafetyResult) -> None:
        pass

    def after_routing(self, request: ChatRequest, intent_result: IntentResult) -> None:
        pass

    def before_action(
        self,
        run_context: RunContext,
        harness_action: HarnessAction,
        permission_decision: PermissionDecision,
    ) -> HookDecision | None:
        return None

    def after_action(
        self,
        run_context: RunContext,
        harness_action: HarnessAction,
        skill_result: SkillResult,
    ) -> None:
        pass

    def after_skill(self, request: ChatRequest, skill_result: SkillResult) -> None:
        pass

    def before_memory_write(
        self,
        run_context: RunContext,
        memory_kind: str,
        payload: dict[str, object],
    ) -> HookDecision | None:
        return None

    def after_trace(self, trace: TraceRecord) -> None:
        pass

    def on_stop(self, run_context: RunContext, trace: TraceRecord) -> None:
        pass
