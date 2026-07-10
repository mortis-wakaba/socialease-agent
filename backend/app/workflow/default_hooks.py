"""Default production-style hooks for privacy and lightweight metrics."""

from __future__ import annotations

from app.db.factory import repository_factory
from app.models import ChatRequest, RiskLevel, TraceRecord
from app.observability.metrics import (
    HarnessMetricsSnapshot,
    InMemoryMetricsRepository,
    MetricsRepository,
)
from app.privacy.redaction import detect_sensitive_categories
from app.safety.actions import HarnessAction
from app.safety.permissions import PermissionDecision
from app.skills import SkillResult
from app.workflow.context import RunContext
from app.workflow.hooks import AgentHarnessHook, HookDecision, NullHarnessHook


class PrivacyGuardHook(NullHarnessHook):
    """Prevent sensitive identifiers from being written into memory."""

    def before_memory_write(
        self,
        run_context: RunContext,
        memory_kind: str,
        payload: dict[str, object],
    ) -> HookDecision | None:
        """Block memory writes when user text contains sensitive identifiers."""
        detected = _detect_sensitive_labels(run_context.message)
        if not detected:
            return None
        return HookDecision(
            allow=False,
            reason=f"privacy_guard_detected:{','.join(detected)}",
            structured_data={
                "privacy_guard_blocked": True,
                "privacy_guard_detected": detected,
                "memory_kind": memory_kind,
            },
        )


class MetricsHook(NullHarnessHook):
    """Collect lightweight, non-identifying harness metrics through a backend."""

    def __init__(self, repository: MetricsRepository | None = None) -> None:
        self.repository = repository or InMemoryMetricsRepository()

    def before_safety(self, request: ChatRequest) -> None:
        """Accept lifecycle compatibility without storing user text."""
        return None

    def after_action(
        self,
        run_context: RunContext,
        harness_action: HarnessAction,
        skill_result: SkillResult,
    ) -> None:
        """Keep the hook active at the action boundary without payload capture."""
        return None

    def after_trace(self, trace: TraceRecord) -> None:
        """Record aggregate counters from the persisted trace."""
        self.repository.record_trace(trace)

    def snapshot(self) -> HarnessMetricsSnapshot:
        """Return a copy of the current aggregate metrics."""
        return self.repository.snapshot()

    def reset(self) -> None:
        """Reset metrics for tests or local demo resets."""
        self.repository.reset()


privacy_guard_hook = PrivacyGuardHook()
metrics_hook = MetricsHook(repository=repository_factory().metrics_repository())


def create_default_hooks() -> tuple[AgentHarnessHook, ...]:
    """Return default hooks in deterministic execution order."""
    return (metrics_hook, privacy_guard_hook)


def _detect_sensitive_labels(text: str) -> list[str]:
    """Return sensitive identifier labels found in text."""
    return detect_sensitive_categories(text)
