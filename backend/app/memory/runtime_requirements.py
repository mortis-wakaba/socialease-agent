"""Deployment requirements for shared short-lived task state."""

from __future__ import annotations

from dataclasses import dataclass
import os

from app.auth.tokens import auth_mode


@dataclass(frozen=True)
class TaskStateRuntimeReport:
    """Describe whether the current deployment has an acceptable Redis setup."""

    required: bool
    configured: bool
    redis_url: str | None

    @property
    def configuration_ok(self) -> bool:
        """Return whether startup may proceed without a silent memory fallback."""
        return self.configured or not self.required


class TaskStateConfigurationError(RuntimeError):
    """Raised when production requires Redis but no Redis URL is configured."""


def task_state_runtime_report() -> TaskStateRuntimeReport:
    """Resolve Redis requirements without exposing the configured URL."""
    redis_url = os.getenv("SOCIALEASE_REDIS_URL", "").strip() or None
    configured_requirement = os.getenv("SOCIALEASE_REQUIRE_REDIS")
    required = (
        _as_bool(configured_requirement)
        if configured_requirement is not None
        else auth_mode() == "production"
    )
    return TaskStateRuntimeReport(
        required=required,
        configured=redis_url is not None,
        redis_url=redis_url,
    )


def validate_task_state_runtime() -> TaskStateRuntimeReport:
    """Fail startup when production task state would otherwise be unavailable."""
    report = task_state_runtime_report()
    if not report.configuration_ok:
        raise TaskStateConfigurationError(
            "SOCIALEASE_REDIS_URL is required when shared Redis task state is enforced."
        )
    return report


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
