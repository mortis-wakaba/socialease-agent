"""Permission gate decisions derived from SocialEase safety classification."""

from enum import Enum

from pydantic import BaseModel

from app.models import RiskLevel, SafetyResult
from app.safety.actions import HarnessAction


class PermissionAction(str, Enum):
    """Actions the harness may take after safety classification."""

    ALLOW = "allow"
    ASK_CONSENT = "ask_consent"
    DOWN_SHIFT = "down_shift"
    BLOCK = "block"
    ESCALATE = "escalate"


class PermissionDecision(BaseModel):
    """Detailed permission decision for one harness action."""

    action: PermissionAction
    reason: str
    required_protocol: str | None = None
    allowed: bool = True
    requires_consent: bool = False
    intensity_adjustment: int | None = None
    escalation_required: bool = False
    block_reason: str | None = None


class SafetyPermissionGate:
    """Convert safety risk into a harness permission decision."""

    def decide(
        self,
        safety_result: SafetyResult,
        harness_action: HarnessAction = HarnessAction.GENERAL_SUPPORT,
    ) -> PermissionDecision:
        """Return whether the harness may execute one bounded action."""
        if safety_result.risk_level == RiskLevel.CRISIS:
            return PermissionDecision(
                action=PermissionAction.ESCALATE,
                reason="Crisis risk always requires escalation before ordinary actions.",
                required_protocol="crisis_escalation",
                allowed=False,
                escalation_required=True,
                block_reason="Crisis escalation is required before ordinary actions.",
            )

        if harness_action == HarnessAction.WRITE_MEMORY:
            return PermissionDecision(
                action=PermissionAction.ASK_CONSENT,
                reason="Long-term memory writes require explicit user consent.",
                required_protocol="memory_write_consent",
                allowed=False,
                requires_consent=True,
            )

        if harness_action == HarnessAction.COMPLETE_EXPOSURE_TASK:
            return PermissionDecision(
                action=PermissionAction.ASK_CONSENT,
                reason="Recording exposure practice feedback changes progress state and requires consent.",
                required_protocol="complete_exposure_task_consent",
                allowed=False,
                requires_consent=True,
            )

        if safety_result.risk_level == RiskLevel.HIGH:
            if harness_action in {
                HarnessAction.START_ROLEPLAY,
                HarnessAction.CREATE_EXPOSURE_PLAN,
                HarnessAction.PROPOSE_CALENDAR_EVENT,
            }:
                return PermissionDecision(
                    action=PermissionAction.BLOCK,
                    reason="High-risk states should not start active practice or calendar planning.",
                    allowed=False,
                    block_reason="High-risk states should not start active practice planning.",
                )
            return PermissionDecision(
                action=PermissionAction.ALLOW,
                reason="High-risk state allows support/resource guidance but not higher-intensity practice.",
            )

        if safety_result.risk_level == RiskLevel.MEDIUM:
            if harness_action == HarnessAction.START_ROLEPLAY:
                return PermissionDecision(
                    action=PermissionAction.ASK_CONSENT,
                    reason="Medium risk role-play should start only after explicit consent.",
                    required_protocol="start_roleplay_consent",
                    allowed=False,
                    requires_consent=True,
                )
            if harness_action == HarnessAction.CREATE_EXPOSURE_PLAN:
                return PermissionDecision(
                    action=PermissionAction.ASK_CONSENT,
                    reason=(
                        "Medium risk exposure planning should ask consent and use a "
                        "lower-intensity first step."
                    ),
                    required_protocol="create_exposure_plan_consent",
                    allowed=False,
                    requires_consent=True,
                    intensity_adjustment=-2,
                )
            return PermissionDecision(
                action=PermissionAction.ALLOW,
                reason="Medium risk allows worksheet, support, and resource guidance.",
            )

        if harness_action in {
            HarnessAction.START_ROLEPLAY,
            HarnessAction.CREATE_EXPOSURE_PLAN,
        }:
            return PermissionDecision(
                action=PermissionAction.ASK_CONSENT,
                reason="Starting a new practice session should ask for consent first.",
                required_protocol=f"{harness_action.value}_consent",
                allowed=False,
                requires_consent=True,
            )

        return PermissionDecision(
            action=PermissionAction.ALLOW,
            reason="Low-risk action allowed by permission gate.",
        )
