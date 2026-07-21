"""Central privacy sanitizer for free-text fields in product traces."""

from __future__ import annotations

import re
from typing import Any

from app.models import TraceFieldPolicy, TraceRecord
from app.privacy.redaction import redact_sensitive_identifiers


_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|authorization|bearer|access[_-]?token|"
        r"refresh[_-]?token|secret|password)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
    ),
)


class TraceSanitizer:
    """Redact trace metadata that may contain model- or provider-authored text."""

    def sanitize(self, record: TraceRecord) -> TraceRecord:
        """Return a deep-copied trace with all supported free-text fields sanitized."""
        sanitized = record.model_copy(deep=True)
        policies = list(sanitized.privacy_summary.fields)

        sanitized.safety_result.reason = self._field(
            field="safety_result.reason",
            value=sanitized.safety_result.reason,
            policies=policies,
        )
        sanitized.intent_result.reason = self._field(
            field="intent_result.reason",
            value=sanitized.intent_result.reason,
            policies=policies,
        )
        if sanitized.permission_reason is not None:
            sanitized.permission_reason = self._field(
                field="permission_reason",
                value=sanitized.permission_reason,
                policies=policies,
            )

        sanitized.errors = [
            self._error_field(field=f"errors[{index}]", value=value, policies=policies)
            for index, value in enumerate(sanitized.errors)
        ]
        sanitized.agent_loop_steps = [
            self._nested(
                value=step,
                field=f"agent_loop_steps[{index}]",
                policies=policies,
            )
            for index, step in enumerate(sanitized.agent_loop_steps)
        ]
        sanitized.privacy_summary.fields = policies
        sanitized.product_safe = True
        return sanitized

    def _error_field(
        self,
        *,
        field: str,
        value: str,
        policies: list[TraceFieldPolicy],
    ) -> str:
        """Keep stable category/class markers and remove legacy exception messages."""
        category, separator, detail = value.partition(":")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", category):
            return self._field(field=field, value=value, policies=policies)
        if (
            separator
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", detail)
        ):
            return value
        replacement = f"{category or 'UNKNOWN_FAILURE'}:[redacted:error_detail]"
        policies.append(
            TraceFieldPolicy(
                field=field,
                persistence_kind="trace_metadata",
                policy="minimized",
                redacted_types=["error_detail"],
                original_length=len(value),
                persisted_length=len(replacement),
                minimized=True,
            )
        )
        return replacement

    def _nested(
        self,
        *,
        value: Any,
        field: str,
        policies: list[TraceFieldPolicy],
    ) -> Any:
        if isinstance(value, str):
            return self._field(field=field, value=value, policies=policies)
        if isinstance(value, list):
            return [
                self._nested(
                    value=item,
                    field=f"{field}[{index}]",
                    policies=policies,
                )
                for index, item in enumerate(value)
            ]
        if isinstance(value, dict):
            return {
                key: self._nested(
                    value=item,
                    field=f"{field}.{key}",
                    policies=policies,
                )
                for key, item in value.items()
            }
        return value

    def _field(
        self,
        *,
        field: str,
        value: str,
        policies: list[TraceFieldPolicy],
    ) -> str:
        sanitized, detected = _sanitize_trace_text(value)
        if sanitized == value:
            return value
        policies.append(
            TraceFieldPolicy(
                field=field,
                persistence_kind="trace_metadata",
                policy="redact_only",
                redacted_types=detected,
                original_length=len(value),
                persisted_length=len(sanitized),
            )
        )
        return sanitized


def _sanitize_trace_text(text: str) -> tuple[str, list[str]]:
    """Redact identifiers and secret-shaped values from one trace metadata field."""
    sanitized, detected = redact_sensitive_identifiers(text)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(sanitized):
            sanitized = pattern.sub("[redacted:secret]", sanitized)
            if "secret" not in detected:
                detected.append("secret")
    return sanitized, detected


trace_sanitizer = TraceSanitizer()
