"""Capability discovery routes for the SocialEase agent harness."""

from pathlib import Path

from fastapi import APIRouter, Query

from app.models_harness import (
    HarnessCapabilitiesResponse,
    HarnessMetricsResponse,
    HarnessSkillCapability,
)
from app.models_knowledge import KnowledgeBaseType
from app.safety.permissions import PermissionAction
from app.skills.registry import skill_registry
from app.tracing.logger import trace_logger

router = APIRouter(prefix="/harness", tags=["harness"])


@router.get("/capabilities", response_model=HarnessCapabilitiesResponse)
async def get_harness_capabilities() -> HarnessCapabilitiesResponse:
    """Return the harness loop, permissions, skills, and knowledge layers."""
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
            "SkillRegistry",
            "SkillExecution",
            "TraceLogger",
            "after_trace_hooks",
        ],
        permission_actions=[action.value for action in PermissionAction],
        skills=skills,
        knowledge_layers=list(KnowledgeBaseType),
        observation=[
            "TraceLogger",
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
) -> HarnessMetricsResponse:
    """Return lightweight aggregate metrics for recent harness runs."""
    records = trace_logger.list_recent(limit=limit)
    intent_counts: dict[str, int] = {}
    selected_agent_counts: dict[str, int] = {}
    fallback_runs = 0
    crisis_runs = 0
    total_latency = 0.0

    for record in records:
        intent = record.intent_result.intent.value
        intent_counts[intent] = intent_counts.get(intent, 0) + 1
        selected_agent_counts[record.selected_agent] = (
            selected_agent_counts.get(record.selected_agent, 0) + 1
        )
        total_latency += record.latency_ms
        if record.safety_result.risk_level.value == "crisis":
            crisis_runs += 1
        if (
            record.safety_result.llm_usage.fallback_used
            or record.intent_result.llm_usage.fallback_used
        ):
            fallback_runs += 1

    total_runs = len(records)
    return HarnessMetricsResponse(
        window_size=limit,
        total_runs=total_runs,
        crisis_runs=crisis_runs,
        fallback_runs=fallback_runs,
        average_latency_ms=(total_latency / total_runs if total_runs else 0.0),
        intent_counts=intent_counts,
        selected_agent_counts=selected_agent_counts,
    )
