"""Lightweight lifecycle hooks around the SocialEase agent harness loop."""

from typing import Protocol

from app.models import ChatRequest, IntentResult, SafetyResult, TraceRecord
from app.skills import SkillResult


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

    def after_skill(self, request: ChatRequest, skill_result: SkillResult) -> None:
        """Observe the selected skill result before trace persistence."""
        ...

    def after_trace(self, trace: TraceRecord) -> None:
        """Observe the final persisted trace record."""
        ...


class NullHarnessHook:
    """No-op hook used as a safe default extension point."""

    def before_safety(self, request: ChatRequest) -> None:
        pass

    def after_safety(self, request: ChatRequest, safety_result: SafetyResult) -> None:
        pass

    def after_routing(self, request: ChatRequest, intent_result: IntentResult) -> None:
        pass

    def after_skill(self, request: ChatRequest, skill_result: SkillResult) -> None:
        pass

    def after_trace(self, trace: TraceRecord) -> None:
        pass
