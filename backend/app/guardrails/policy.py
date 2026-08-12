"""Choose blocking output checks and delivery behavior for one response."""

from dataclasses import dataclass
from enum import Enum

from app.models import Intent, RiskLevel


class OutputGuardrailMode(str, Enum):
    """Supported levels of synchronous output enforcement."""

    DETERMINISTIC_ONLY = "deterministic_only"
    FULL = "full"


@dataclass(frozen=True)
class OutputGuardrailPolicyDecision:
    """Application-owned guardrail and response-delivery decision."""

    mode: OutputGuardrailMode
    sentence_streaming_allowed: bool
    reason: str


class OutputGuardrailPolicy:
    """Keep semantic checks on sensitive paths while allowing safe streaming."""

    def decide(
        self,
        *,
        streaming_requested: bool,
        risk_level: RiskLevel,
        intent: Intent,
        selected_skill: str,
        memory_evidence: list[str],
    ) -> OutputGuardrailPolicyDecision:
        """Return a conservative policy from trusted runtime facts."""
        if not streaming_requested:
            return _full("non_streaming_request")
        if risk_level is not RiskLevel.LOW:
            return _full("elevated_risk")
        if memory_evidence:
            return _full("memory_grounded_response")
        if intent is Intent.CAMPUS_RESOURCE_QUERY or "resource" in selected_skill:
            return _full("resource_grounding_required")
        if selected_skill != "general_support_skill":
            return _full("specialized_skill")
        if intent not in {Intent.EMOTIONAL_SUPPORT, Intent.PROGRESS_REVIEW}:
            return _full("non_streamable_intent")
        return OutputGuardrailPolicyDecision(
            mode=OutputGuardrailMode.DETERMINISTIC_ONLY,
            sentence_streaming_allowed=True,
            reason="low_risk_general_support",
        )


def _full(reason: str) -> OutputGuardrailPolicyDecision:
    return OutputGuardrailPolicyDecision(
        mode=OutputGuardrailMode.FULL,
        sentence_streaming_allowed=False,
        reason=reason,
    )
