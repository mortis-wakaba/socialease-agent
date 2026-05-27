"""Permission gate decisions derived from SocialEase safety classification."""

from enum import Enum

from app.models import RiskLevel, SafetyResult


class PermissionAction(str, Enum):
    """Actions the harness may take after safety classification."""

    ALLOW = "allow"
    ESCALATE = "escalate"


class SafetyPermissionGate:
    """Convert safety risk into a harness permission decision."""

    def decide(self, safety_result: SafetyResult) -> PermissionAction:
        """Return whether the harness may continue or must escalate."""
        if safety_result.risk_level == RiskLevel.CRISIS:
            return PermissionAction.ESCALATE
        return PermissionAction.ALLOW
