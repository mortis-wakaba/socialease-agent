"""Per-run context loaded and passed through the lead agent harness."""

from dataclasses import dataclass, field
from typing import Any

from app.models import IntentResult, SafetyResult
from app.models_context import SkillContextProjection
from app.models_exposure import ExposurePlan
from app.models_intervention import InterventionPlan
from app.models_memory import MemoryContext, UserPracticeSummary
from app.models_support_generation import PresentationConstraints


@dataclass
class RunContext:
    """State bundle for one harness run."""

    run_id: str
    user_id: str
    session_id: str | None
    message: str
    request_context: dict[str, Any]
    safety_result: SafetyResult | None = None
    intent_result: IntentResult | None = None
    user_profile: UserPracticeSummary | None = None
    active_exposure_plan: ExposurePlan | None = None
    memory_context: MemoryContext | None = None
    skill_context: SkillContextProjection | None = None
    consent_state: dict[str, Any] = field(default_factory=dict)
    intervention_plan: InterventionPlan | None = None
    response_constraints: PresentationConstraints = field(
        default_factory=PresentationConstraints
    )
