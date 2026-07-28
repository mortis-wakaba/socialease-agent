"""Privacy-aware gate for user-derived persisted text."""

from app.db.factory import repository_factory
from app.memory.settings_store import UserMemorySettingsRepository
from app.privacy.policy import (
    MINIMIZED_TEXT_BY_KIND,
    RAW_MESSAGE_KINDS,
    PersistenceDecision,
    PersistenceKind,
    TraceOutputPolicy,
    trace_output_policy_from_env,
)
from app.privacy.redaction import redact_sensitive_identifiers


class PersistenceGate:
    """Apply user memory settings before text is saved to persistence."""

    def __init__(self, repository: UserMemorySettingsRepository | None = None) -> None:
        self.repository = repository or repository_factory().user_memory_settings_repository()

    async def persist_text(
        self,
        *,
        user_id: str,
        kind: PersistenceKind,
        text: str,
    ) -> PersistenceDecision:
        """Return a safe text value for persistence."""
        settings = await self.repository.get(user_id)
        redacted, detected = redact_sensitive_identifiers(text)
        if settings.consent_state.allow_sensitive_memory:
            redacted = text
            detected = []

        if kind == PersistenceKind.TRACE_OUTPUT:
            return self._persist_trace_output(
                text=text,
                redacted=redacted,
                detected=detected,
            )

        if settings.consent_state.do_not_store_raw_messages and kind in RAW_MESSAGE_KINDS:
            return PersistenceDecision(
                kind=kind,
                original_length=len(text),
                persisted_text=MINIMIZED_TEXT_BY_KIND[kind],
                minimized=True,
                policy="minimized",
                redacted_types=detected,
            )

        return PersistenceDecision(
            kind=kind,
            original_length=len(text),
            persisted_text=redacted,
            minimized=False,
            policy="redact_only",
            redacted_types=detected,
        )

    def _persist_trace_output(
        self,
        *,
        text: str,
        redacted: str,
        detected: list[str],
    ) -> PersistenceDecision:
        """Apply the configured trace-output persistence strategy."""
        policy = trace_output_policy_from_env()
        if policy == TraceOutputPolicy.MINIMIZED:
            return PersistenceDecision(
                kind=PersistenceKind.TRACE_OUTPUT,
                original_length=len(text),
                persisted_text=MINIMIZED_TEXT_BY_KIND[PersistenceKind.TRACE_OUTPUT],
                minimized=True,
                policy=policy.value,
                redacted_types=detected,
            )
        if policy == TraceOutputPolicy.SUMMARY_ONLY:
            return PersistenceDecision(
                kind=PersistenceKind.TRACE_OUTPUT,
                original_length=len(text),
                persisted_text=_summarize_trace_output(redacted),
                summarized=True,
                policy=policy.value,
                redacted_types=detected,
            )
        return PersistenceDecision(
            kind=PersistenceKind.TRACE_OUTPUT,
            original_length=len(text),
            persisted_text=redacted,
            minimized=False,
            policy=policy.value,
            redacted_types=detected,
        )

    async def persist_texts(
        self,
        *,
        user_id: str,
        kind: PersistenceKind,
        texts: list[str],
    ) -> list[str]:
        """Apply the same persistence policy to a list of texts."""
        return [
            (
                await self.persist_text(user_id=user_id, kind=kind, text=text)
            ).persisted_text
            for text in texts
        ]


persistence_gate = PersistenceGate()


def _summarize_trace_output(text: str) -> str:
    """Return a short non-verbatim summary label for assistant trace output."""
    normalized = text.casefold()
    if "crisis" in normalized or "紧急服务" in text or "伤害自己" in text:
        category = "crisis_escalation"
    elif "同意" in text or "consent" in normalized:
        category = "consent_or_permission"
    elif "角色扮演" in text or "role-play" in normalized or "roleplay" in normalized:
        category = "roleplay_practice"
    elif "worksheet" in normalized or "自动想法" in text or "自助反思" in text:
        category = "worksheet_reflection"
    elif "阶梯" in text or "分级" in text or "练习计划" in text:
        category = "exposure_practice_plan"
    elif "资源" in text or "引用" in text or "citation" in normalized:
        category = "resource_navigation"
    else:
        category = "supportive_response"
    return (
        "[assistant output summarized by privacy policy: "
        f"{category}; raw assistant text not retained]"
    )
