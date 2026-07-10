"""Workflow-level error recovery helpers for the agent harness."""

from __future__ import annotations

from enum import Enum

from app.llm.retry import ProviderError
from app.safety.actions import HarnessAction
from app.skills import SkillResult


class ErrorCategory(str, Enum):
    """Stable error categories written into traces."""

    TRANSIENT_PROVIDER_ERROR = "TRANSIENT_PROVIDER_ERROR"
    INVALID_JSON = "INVALID_JSON"
    SAFETY_CLASSIFIER_FAILURE = "SAFETY_CLASSIFIER_FAILURE"
    TOOL_OR_SKILL_FAILURE = "TOOL_OR_SKILL_FAILURE"
    MEMORY_WRITE_FAILURE = "MEMORY_WRITE_FAILURE"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


def categorize_error(error: Exception) -> ErrorCategory:
    """Map runtime exceptions into stable trace categories."""
    if isinstance(error, ProviderError):
        return ErrorCategory.TRANSIENT_PROVIDER_ERROR
    if isinstance(error, ValueError) and "JSON" in str(error).upper():
        return ErrorCategory.INVALID_JSON
    return ErrorCategory.UNKNOWN_FAILURE


def format_trace_error(category: ErrorCategory, error: Exception | str) -> str:
    """Return compact trace-safe error text."""
    message = str(error)
    if not message:
        message = error.__class__.__name__ if isinstance(error, Exception) else "unknown"
    return f"{category.value}:{message[:160]}"


def skill_failure_result(
    *,
    harness_action: HarnessAction,
    category: ErrorCategory,
) -> SkillResult:
    """Return a safe bounded response when a skill/tool execution fails."""
    return SkillResult(
        response=(
            "这一步暂时没有成功完成。你可以先停在这里，稍后重试；"
            "如果你现在压力很高，建议先联系可信任的人或学校支持资源。"
        ),
        structured_data={
            "agent": "lead_harness",
            "action": "skill_failed",
            "blocked": True,
            "harness_action": harness_action.value,
            "error_category": category.value,
            "fallback_used": True,
        },
        selected_agent="lead_harness",
    )
