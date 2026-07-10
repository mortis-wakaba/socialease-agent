"""Capability discovery routes for the SocialEase agent harness."""

from pathlib import Path

from fastapi import APIRouter, Depends, Query

from app.auth.context import AuthContext
from app.auth.dependencies import get_optional_current_user, require_developer_access
from app.models_harness import (
    HarnessCapabilitiesResponse,
    HarnessMetricsResponse,
    HarnessSkillCapability,
)
from app.models_knowledge import KnowledgeBaseType
from app.safety.permissions import PermissionAction
from app.skills.registry import skill_registry
from app.workflow.default_hooks import metrics_hook


router = APIRouter(prefix="/harness", tags=["harness"])


@router.get("/capabilities", response_model=HarnessCapabilitiesResponse)
async def get_harness_capabilities(
    current_user: AuthContext = Depends(get_optional_current_user),
) -> HarnessCapabilitiesResponse:
    """Return the harness loop, permissions, skills, and knowledge layers."""
    require_developer_access(current_user)
    skills = [
        HarnessSkillCapability(
            name=descriptor.name,
            description=descriptor.description,
            supported_intents=list(descriptor.supported_intents),
            entrypoint=descriptor.entrypoint,
            safety_notes=descriptor.safety_notes,
            has_manifest=(
                descriptor.manifest_path is not None
                and Path(descriptor.manifest_path).exists()
            ),
        )
        for descriptor in skill_registry.list_descriptors()
    ]

    return HarnessCapabilitiesResponse(
        runtime_loop=[
            "AgentHarness",
            "before_safety_hooks",
            "SafetyClassifier",
            "SafetyPermissionGate",
            "IntentRouter_or_Escalation",
            "before_action_hooks",
            "SkillRegistry",
            "SkillExecution",
            "before_memory_write_hooks",
            "TraceLogger",
            "after_trace_hooks",
            "on_stop_hooks",
        ],
        permission_actions=[action.value for action in PermissionAction],
        skills=skills,
        knowledge_layers=list(KnowledgeBaseType),
        observation=[
            "TraceLogger",
            "MetricsHook",
            "PrivacyGuardHook",
            "GET /api/runs/{run_id}",
            "llm_usage",
            "backend/app/evals",
        ],
        safety_boundaries=[
            "no_diagnosis",
            "no_treatment_promise",
            "crisis_escalation_required",
            "no_fake_campus_resources",
            "deterministic_safety_floor",
        ],
    )


@router.get("/metrics", response_model=HarnessMetricsResponse)
async def get_harness_metrics(
    limit: int = Query(default=100, ge=1, le=500),
    current_user: AuthContext = Depends(get_optional_current_user),
) -> HarnessMetricsResponse:
    """Return lightweight aggregate metrics captured by MetricsHook."""
    require_developer_access(current_user)
    snapshot = metrics_hook.snapshot()
    return HarnessMetricsResponse(
        window_size=limit,
        total_runs=snapshot.total_runs,
        crisis_runs=snapshot.crisis_runs,
        fallback_runs=snapshot.fallback_runs,
        hook_blocked_runs=snapshot.hook_blocked_runs,
        memory_write_blocked_runs=snapshot.memory_write_blocked_runs,
        average_latency_ms=snapshot.average_latency_ms,
        latency_p50_ms=snapshot.latency_p50_ms,
        latency_p95_ms=snapshot.latency_p95_ms,
        intent_counts=snapshot.intent_counts,
        risk_counts=snapshot.risk_counts,
        selected_agent_counts=snapshot.selected_agent_counts,
        permission_counts=snapshot.permission_counts,
        product_boundary_eval_counts=snapshot.product_boundary_eval_counts,
        runtime_event_counts=snapshot.runtime_event_counts,
        rate_limit_hits=snapshot.rate_limit_hits,
        llm_concurrency_saturation=snapshot.llm_concurrency_saturation,
        slow_request_count=snapshot.slow_request_count,
        memory_export_count=snapshot.memory_export_count,
        memory_delete_count=snapshot.memory_delete_count,
        memory_preferences_saved_count=snapshot.memory_preferences_saved_count,
        memory_preferences_disabled_count=snapshot.memory_preferences_disabled_count,
        auth_rate_limit_hits=snapshot.auth_rate_limit_hits,
        auth_failed_login_count=snapshot.auth_failed_login_count,
        auth_lockout_count=snapshot.auth_lockout_count,
    )
