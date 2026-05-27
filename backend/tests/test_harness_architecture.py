"""Tests for harness permissions, hooks, and on-demand skill manifests."""

import pytest

from app.models import ChatRequest, Intent, RiskLevel, SafetyResult, TraceRecord
from app.safety.permissions import PermissionAction, SafetyPermissionGate
from app.skills.manifest_loader import load_skill_manifest
from app.skills.registry import SkillRegistry
from app.db.repositories import InMemoryTraceRepository
from app.tracing.logger import TraceLogger
from app.workflow.engine import AgentHarness


class RecordingHook:
    """Test hook that records harness lifecycle events."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def before_safety(self, request: ChatRequest) -> None:
        self.events.append("before_safety")

    def after_safety(self, request: ChatRequest, safety_result: SafetyResult) -> None:
        self.events.append(f"after_safety:{safety_result.risk_level.value}")

    def after_routing(self, request: ChatRequest, intent_result: object) -> None:
        self.events.append("after_routing")

    def after_skill(self, request: ChatRequest, skill_result: object) -> None:
        self.events.append("after_skill")

    def after_trace(self, trace: TraceRecord) -> None:
        self.events.append(f"after_trace:{trace.selected_agent}")


def test_permission_gate_escalates_crisis_only() -> None:
    gate = SafetyPermissionGate()

    assert gate.decide(SafetyResult(risk_level=RiskLevel.CRISIS, reason="x")) == PermissionAction.ESCALATE
    assert gate.decide(SafetyResult(risk_level=RiskLevel.LOW, reason="x")) == PermissionAction.ALLOW


def test_skill_manifest_loads_on_demand() -> None:
    registry = SkillRegistry()
    descriptor = registry.descriptor_for_intent(Intent.CBT_WORKSHEET)

    assert descriptor is not None
    manifest = load_skill_manifest(descriptor)

    assert manifest is not None
    assert manifest.skill_name == "worksheet_skill"
    assert "Worksheet Skill" in manifest.content
    assert "Safety boundaries" in manifest.content


@pytest.mark.anyio
async def test_agent_harness_runs_lifecycle_hooks() -> None:
    hook = RecordingHook()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        hooks=(hook,),
    )

    response = await harness.run(
        ChatRequest(
            user_id="demo_user",
            message="我想模拟课堂发言",
            context={},
        )
    )

    assert response.intent == Intent.ROLEPLAY_PRACTICE
    assert hook.events == [
        "before_safety",
        "after_safety:low",
        "after_routing",
        "after_skill",
        "after_trace:support_agent",
    ]
