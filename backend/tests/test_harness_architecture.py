"""Tests for harness permissions, hooks, and on-demand skill manifests."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models import ChatRequest, Intent, RiskLevel, SafetyResult, TraceRecord
from app.models_exposure import ExposurePlan, ExposureTask
from app.models_memory import PracticePreferences, UserConsentState, UserPracticeSummary
from app.memory.context_builder import build_memory_context
from app.memory.settings_store import user_memory_settings_store
from app.protocols.service import protocol_service
from app.safety.actions import HarnessAction
from app.safety.permissions import PermissionAction, PermissionDecision, SafetyPermissionGate
from app.skills.base import SkillContext, SkillDescriptor, SkillResult
from app.skills.manifest_loader import load_skill_manifest
from app.skills.registry import SkillRegistry
from app.db.repositories import InMemoryTraceRepository
from app.tracing.logger import TraceLogger
from app.workflow.engine import AgentHarness
from app.workflow.context import RunContext
from app.workflow.default_hooks import MetricsHook, PrivacyGuardHook
from app.workflow import engine as workflow_engine
from app.workflow.hooks import HookDecision


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


class ContextInspectingSupportSkill:
    """Test skill that verifies RunContext state was loaded before execution."""

    descriptor = SkillDescriptor(
        name="general_support_skill",
        description="Test support skill",
        supported_intents=(Intent.EMOTIONAL_SUPPORT,),
        entrypoint="tests.ContextInspectingSupportSkill",
        safety_notes="test only",
    )

    def __init__(self) -> None:
        self.seen_profile = False

    async def run(self, context: SkillContext) -> SkillResult:
        self.seen_profile = context.run.user_profile is not None
        return SkillResult(
            response="ok",
            structured_data={"agent": "support_agent", "action": "general_support"},
            selected_agent="support_agent",
        )


class ActionBlockingHook:
    """Test hook that blocks one action."""

    def __init__(self) -> None:
        self.after_action_seen = False
        self.on_stop_seen = False

    def before_action(
        self,
        run_context: RunContext,
        harness_action: HarnessAction,
        permission_decision: PermissionDecision,
    ) -> HookDecision:
        return HookDecision(
            allow=False,
            reason="test hook blocked action",
            structured_data={"test_hook": "blocked"},
        )

    def after_action(
        self,
        run_context: RunContext,
        harness_action: HarnessAction,
        skill_result: SkillResult,
    ) -> None:
        self.after_action_seen = True

    def on_stop(self, run_context: RunContext, trace: TraceRecord) -> None:
        self.on_stop_seen = True


class MemoryWriteBlockingHook:
    """Test hook that blocks intervention-plan writes."""

    def __init__(self) -> None:
        self.memory_kind: str | None = None

    def before_memory_write(
        self,
        run_context: RunContext,
        memory_kind: str,
        payload: dict[str, object],
    ) -> HookDecision:
        self.memory_kind = memory_kind
        return HookDecision(allow=False, reason="test hook blocked memory write")


class FailingSafetyClassifier:
    """Test classifier that simulates a safety provider crash."""

    async def classify(self, message: str) -> SafetyResult:
        raise RuntimeError("safety provider unavailable")


class FailingSkill:
    """Test skill that simulates a tool or provider crash."""

    descriptor = SkillDescriptor(
        name="general_support_skill",
        description="Test failing skill",
        supported_intents=(Intent.EMOTIONAL_SUPPORT,),
        entrypoint="tests.FailingSkill",
        safety_notes="test only",
    )

    async def run(self, context: SkillContext) -> SkillResult:
        raise RuntimeError("tool crashed")


class FailingInterventionPlanService:
    """Test memory service that fails during plan creation."""

    def create_for_action(self, **kwargs: object) -> object:
        raise RuntimeError("sqlite locked")


def test_permission_gate_action_matrix() -> None:
    gate = SafetyPermissionGate()

    assert gate.decide(
        SafetyResult(risk_level=RiskLevel.CRISIS, reason="x"),
        HarnessAction.GENERAL_SUPPORT,
    ).action == PermissionAction.ESCALATE
    assert gate.decide(
        SafetyResult(risk_level=RiskLevel.LOW, reason="x"),
        HarnessAction.GENERAL_SUPPORT,
    ).action == PermissionAction.ALLOW
    assert gate.decide(
        SafetyResult(risk_level=RiskLevel.LOW, reason="x"),
        HarnessAction.START_ROLEPLAY,
    ).action == PermissionAction.ASK_CONSENT
    assert gate.decide(
        SafetyResult(risk_level=RiskLevel.MEDIUM, reason="x"),
        HarnessAction.CREATE_EXPOSURE_PLAN,
    ).action == PermissionAction.ASK_CONSENT
    medium_exposure = gate.decide(
        SafetyResult(risk_level=RiskLevel.MEDIUM, reason="x"),
        HarnessAction.CREATE_EXPOSURE_PLAN,
    )
    assert medium_exposure.requires_consent is True
    assert medium_exposure.intensity_adjustment == -2
    assert gate.decide(
        SafetyResult(risk_level=RiskLevel.HIGH, reason="x"),
        HarnessAction.START_ROLEPLAY,
    ).action == PermissionAction.BLOCK


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
        "after_trace:lead_harness",
    ]


@pytest.mark.anyio
async def test_agent_harness_loads_run_context_for_skill() -> None:
    skill = ContextInspectingSupportSkill()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        skill_registry=SkillRegistry(executable_skills=(skill,)),
    )

    response = await harness.run(
        ChatRequest(
            user_id="context_user",
            message="我今天有点紧张",
            context={"session_id": "session_from_client"},
        )
    )

    assert skill.seen_profile is True
    assert response.trace.session_id == "session_from_client"
    assert response.trace.selected_skill == "general_support_skill"
    assert response.trace.action == "general_support"
    assert response.trace.permission_action == "allow"
    assert "memory_context" in response.structured_data


def test_memory_context_builder_combines_profile_preferences_and_active_plan() -> None:
    plan = ExposurePlan(
        plan_id="plan_memory_context",
        user_id="memory_context_user",
        target_scenario="课堂发言",
        current_anxiety_level=7,
        previous_attempts=[],
        tasks=[
            ExposureTask(
                task_id="task_1",
                title="先在纸上写一句开场",
                description="demo",
                difficulty=2,
                estimated_time_minutes=5,
                success_criteria="完成一句开场",
                fallback_task="只写关键词",
            )
        ],
        recommended_next_task_id="task_1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    memory_context = build_memory_context(
        practice_summary=UserPracticeSummary(
            recent_scenarios=["group_discussion"],
            latest_anxiety_level=6,
            preferred_difficulty=2,
        ),
        memory_settings=user_memory_settings_store.get("memory_context_empty").model_copy(
            update={
                "practice_preferences": PracticePreferences(
                    preferred_roleplay_difficulty=4,
                    preferred_feedback_style="gentle_specific",
                    preferred_practice_scenarios=["dorm_conflict"],
                )
            }
        ),
        active_exposure_plan=plan,
    )

    assert memory_context.recent_scenarios == ["dorm_conflict", "group_discussion"]
    assert memory_context.preferred_difficulty == 4
    assert "test@example.com" not in str(memory_context.practice_preferences.model_dump())
    assert memory_context.latest_anxiety_level == 6
    assert memory_context.active_exposure_plan_id == "plan_memory_context"
    assert memory_context.active_exposure_next_task == "先在纸上写一句开场"
    assert "active_exposure_plan_available" in memory_context.context_notes


@pytest.mark.anyio
async def test_roleplay_skill_uses_memory_context_after_consent() -> None:
    user_id = f"memory_context_roleplay_{uuid4().hex}"
    user_memory_settings_store.save(
        user_id=user_id,
        consent_state=UserConsentState(consent_to_save_preferences=True),
        practice_preferences=PracticePreferences(
            preferred_roleplay_difficulty=4,
            preferred_practice_scenarios=["dorm_conflict"],
        ),
    )
    harness = AgentHarness(trace_logger=TraceLogger(repository=InMemoryTraceRepository()))

    consent_response = await harness.run(
        ChatRequest(
            user_id=user_id,
            message="我想做一个角色扮演练习",
            context={},
        )
    )

    protocol_id = consent_response.structured_data["protocol_id"]
    protocol_service.respond(protocol_id=protocol_id, user_id=user_id, approved=True)

    response = await harness.run(
        ChatRequest(
            user_id=user_id,
            message="我想做一个角色扮演练习",
            context={"protocol_id": protocol_id},
        )
    )

    assert response.structured_data["action"] == "roleplay_started"
    assert response.structured_data["difficulty"] == 4
    assert response.structured_data["scenario"] == "dorm_conflict"
    assert response.structured_data["memory_context_used"] is True
    assert response.structured_data["memory_context"]["preferred_difficulty"] == 4


@pytest.mark.anyio
async def test_chat_exposure_plan_uses_plan_id_as_recoverable_session_after_consent() -> None:
    user_id = f"chat_exposure_plan_{uuid4().hex}"
    harness = AgentHarness(trace_logger=TraceLogger(repository=InMemoryTraceRepository()))

    consent_response = await harness.run(
        ChatRequest(
            user_id=user_id,
            message="我想做一个由易到难的社交练习计划，目标是课堂发言",
            context={
                "current_anxiety_level": 6,
                "target_scenario": "classroom_speech",
            },
        )
    )

    protocol_id = consent_response.structured_data["protocol_id"]
    protocol_service.respond(protocol_id=protocol_id, user_id=user_id, approved=True)

    response = await harness.run(
        ChatRequest(
            user_id=user_id,
            message="我想做一个由易到难的社交练习计划，目标是课堂发言",
            context={
                "protocol_id": protocol_id,
                "current_anxiety_level": 6,
                "target_scenario": "classroom_speech",
            },
        )
    )

    plan_id = response.structured_data["plan_id"]
    intervention_plan = response.structured_data["intervention_plan"]

    assert response.structured_data["action"] == "exposure_plan_created"
    assert plan_id
    assert response.structured_data["session_id"] == plan_id
    assert intervention_plan["session_id"] == plan_id
    assert response.trace.session_id == plan_id


@pytest.mark.anyio
async def test_before_action_hook_can_block_action() -> None:
    hook = ActionBlockingHook()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        hooks=(hook,),
    )

    response = await harness.run(
        ChatRequest(
            user_id="hook_block_user",
            message="我想做一个自动想法 worksheet，情境是小组讨论前很紧张",
            context={},
        )
    )

    assert response.trace.selected_agent == "lead_harness"
    assert response.trace.selected_skill == "lead_harness"
    assert response.structured_data["hook_blocked"] is True
    assert response.structured_data["test_hook"] == "blocked"
    assert "before_action_blocked:test hook blocked action" in response.trace.errors
    assert hook.after_action_seen is True
    assert hook.on_stop_seen is True


@pytest.mark.anyio
async def test_before_action_hook_cannot_block_crisis_escalation() -> None:
    hook = ActionBlockingHook()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        hooks=(hook,),
    )

    response = await harness.run(
        ChatRequest(
            user_id="hook_crisis_user",
            message="我想自杀，撑不下去了",
            context={},
        )
    )

    assert response.risk_level == RiskLevel.CRISIS
    assert response.intent == Intent.CRISIS
    assert response.trace.selected_agent == "crisis_escalation"
    assert "before_action_blocked:test hook blocked action" not in response.trace.errors
    assert response.structured_data.get("hook_blocked") is None


@pytest.mark.anyio
async def test_before_memory_write_hook_can_block_intervention_plan() -> None:
    hook = MemoryWriteBlockingHook()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        hooks=(hook,),
    )

    response = await harness.run(
        ChatRequest(
            user_id="hook_memory_user",
            message="我想做一个自动想法 worksheet，情境是小组讨论前很紧张",
            context={},
        )
    )

    assert hook.memory_kind == "intervention_plan"
    assert response.trace.intervention_plan_id is None
    assert "intervention_plan_id" not in response.structured_data
    assert "before_memory_write_blocked:test hook blocked memory write" in response.trace.errors


@pytest.mark.anyio
async def test_before_memory_write_hook_is_not_called_for_crisis() -> None:
    hook = MemoryWriteBlockingHook()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        hooks=(hook,),
    )

    response = await harness.run(
        ChatRequest(
            user_id="hook_memory_crisis_user",
            message="我想伤害自己",
            context={},
        )
    )

    assert response.risk_level == RiskLevel.CRISIS
    assert hook.memory_kind is None
    assert response.trace.intervention_plan_id is None
    assert not response.trace.errors


@pytest.mark.anyio
async def test_privacy_guard_hook_blocks_sensitive_memory_write() -> None:
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        hooks=(PrivacyGuardHook(),),
    )

    response = await harness.run(
        ChatRequest(
            user_id="privacy_user",
            message="我想做一个自动想法 worksheet，我的邮箱是 test@example.com，情境是作业展示前很紧张",
            context={},
        )
    )

    assert response.intent == Intent.CBT_WORKSHEET
    assert response.trace.intervention_plan_id is None
    assert "intervention_plan_id" not in response.structured_data
    assert response.structured_data["privacy_guard_blocked"] is True
    assert response.structured_data["privacy_guard_detected"] == ["email"]
    assert "before_memory_write_blocked:privacy_guard_detected:email" in response.trace.errors


@pytest.mark.anyio
async def test_metrics_hook_collects_non_identifying_trace_metrics() -> None:
    hook = MetricsHook()
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        hooks=(hook,),
    )

    await harness.run(
        ChatRequest(
            user_id="metrics_hook_user",
            message="我今天有点紧张",
            context={},
        )
    )
    await harness.run(
        ChatRequest(
            user_id="metrics_hook_user",
            message="我不想活了，可能会伤害自己",
            context={},
        )
    )

    snapshot = hook.snapshot()

    assert snapshot.total_runs == 2
    assert snapshot.crisis_runs == 1
    assert snapshot.intent_counts["emotional_support"] == 1
    assert snapshot.intent_counts["crisis"] == 1
    assert snapshot.risk_counts["crisis"] == 1
    assert snapshot.selected_agent_counts["crisis_escalation"] == 1
    assert snapshot.average_latency_ms >= 0


@pytest.mark.anyio
async def test_safety_classifier_failure_fails_closed_to_high_risk() -> None:
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        safety_classifier=FailingSafetyClassifier(),
    )

    response = await harness.run(
        ChatRequest(
            user_id="safety_failure_user",
            message="我想模拟课堂发言",
            context={},
        )
    )

    assert response.risk_level == RiskLevel.HIGH
    assert response.trace.permission_action == "block"
    assert response.structured_data["action"] == "action_blocked"
    assert any(
        error.startswith("SAFETY_CLASSIFIER_FAILURE:")
        for error in response.trace.errors
    )
    assert response.trace.error_categories == ["SAFETY_CLASSIFIER_FAILURE"]
    assert response.structured_data["error_categories"] == ["SAFETY_CLASSIFIER_FAILURE"]


@pytest.mark.anyio
async def test_skill_failure_returns_safe_recovery_response() -> None:
    harness = AgentHarness(
        trace_logger=TraceLogger(repository=InMemoryTraceRepository()),
        skill_registry=SkillRegistry(executable_skills=(FailingSkill(),)),
    )

    response = await harness.run(
        ChatRequest(
            user_id="skill_failure_user",
            message="我今天有点紧张",
            context={},
        )
    )

    assert response.trace.selected_agent == "lead_harness"
    assert response.structured_data["action"] == "skill_failed"
    assert response.structured_data["error_category"] == "TOOL_OR_SKILL_FAILURE"
    assert response.structured_data["fallback_used"] is True
    assert any(
        error.startswith("TOOL_OR_SKILL_FAILURE:")
        for error in response.trace.errors
    )
    assert response.trace.error_categories == ["TOOL_OR_SKILL_FAILURE"]
    assert response.structured_data["error_categories"] == ["TOOL_OR_SKILL_FAILURE"]


@pytest.mark.anyio
async def test_memory_write_failure_is_reported_without_failing_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_engine,
        "intervention_plan_service",
        FailingInterventionPlanService(),
    )
    harness = AgentHarness(trace_logger=TraceLogger(repository=InMemoryTraceRepository()))

    response = await harness.run(
        ChatRequest(
            user_id="memory_failure_user",
            message="我想做一个自动想法 worksheet，情境是课堂展示前紧张",
            context={},
        )
    )

    assert response.intent == Intent.CBT_WORKSHEET
    assert response.trace.intervention_plan_id is None
    assert response.structured_data["memory_write_failed"] is True
    assert response.structured_data["memory_error_category"] == "MEMORY_WRITE_FAILURE"
    assert any(
        error.startswith("MEMORY_WRITE_FAILURE:")
        for error in response.trace.errors
    )
