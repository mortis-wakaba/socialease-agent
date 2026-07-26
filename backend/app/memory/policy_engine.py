"""Deterministic policy separating model proposals from durable writes."""

import re

from app.models import RiskLevel
from app.models_long_term_memory import (
    MemoryEvidenceType,
    MemoryPolicyAction,
    MemoryPolicyDecision,
    MemoryPolicyReason,
    MemoryProposal,
    MemoryProposalOperation,
    MemoryType,
)
from app.models_memory import UserConsentState
from app.privacy.redaction import redact_sensitive_identifiers


_DIAGNOSIS_OR_TRAUMA_PATTERNS = (
    re.compile(r"(?:诊断|确诊|患有).{0,12}(?:症|障碍|疾病)"),
    re.compile(r"(?:抑郁症|焦虑症|社交焦虑症|人格障碍|创伤后应激)"),
    re.compile(r"(?:童年创伤|人格缺陷|依恋类型).{0,16}(?:导致|说明|证明|所以)"),
)
_CRISIS_PATTERNS = (
    re.compile(r"(?:自杀|自伤|不想活|结束生命|伤害自己|伤害他人)"),
)
_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions?", re.I),
    re.compile(r"(?:忽略|覆盖|绕过).{0,12}(?:系统|之前|安全|记忆).{0,8}(?:指令|规则|策略)"),
    re.compile(r"(?:system\s*prompt|developer\s*message|系统提示词|开发者消息)", re.I),
    re.compile(r"(?:直接|强制).{0,8}(?:写入|保存).{0,8}(?:数据库|memory|长期记忆)", re.I),
)


class MemoryPolicyEngine:
    """Apply product-owned safety, consent, and evidence rules."""

    def decide(
        self,
        proposal: MemoryProposal,
        *,
        consent_state: UserConsentState,
        risk_level: RiskLevel,
        explicit_revoke_requested: bool = False,
    ) -> MemoryPolicyDecision:
        """Return a deterministic decision; never perform persistence."""
        summary = " ".join(proposal.summary.split())
        redacted, detected = redact_sensitive_identifiers(summary)

        if risk_level == RiskLevel.CRISIS or _matches_any(summary, _CRISIS_PATTERNS):
            return _reject(
                proposal,
                MemoryPolicyReason.CRISIS_CONTENT_REJECTED,
                detected,
            )
        if _matches_any(summary, _DIAGNOSIS_OR_TRAUMA_PATTERNS):
            return _reject(
                proposal,
                MemoryPolicyReason.DIAGNOSIS_OR_TRAUMA_INFERENCE_REJECTED,
                detected,
            )
        if detected:
            return _reject(
                proposal,
                MemoryPolicyReason.THIRD_PARTY_OR_IDENTIFIER_REJECTED,
                detected,
            )
        if _matches_any(summary, _PROMPT_INJECTION_PATTERNS):
            return _reject(
                proposal,
                MemoryPolicyReason.PROMPT_INJECTION_REJECTED,
                [],
            )
        if proposal.confidence < 0.7:
            return _reject(
                proposal,
                MemoryPolicyReason.LOW_CONFIDENCE_REJECTED,
                [],
            )
        if proposal.operation == MemoryProposalOperation.REVOKE:
            if not explicit_revoke_requested:
                return _reject(
                    proposal,
                    MemoryPolicyReason.EXPLICIT_REVOCATION_REQUIRED,
                    [],
                )
            if (
                proposal.evidence_type
                in {
                    MemoryEvidenceType.EXPLICIT_USER_STATEMENT,
                    MemoryEvidenceType.USER_CONFIRMED,
                }
                and proposal.confidence >= 0.8
            ):
                return MemoryPolicyDecision(
                    proposal_id=proposal.proposal_id,
                    action=MemoryPolicyAction.REVOKE,
                    reason=MemoryPolicyReason.EXPLICIT_REVOCATION_ALLOWED,
                    safe_summary=redacted,
                )
            return _reject(
                proposal,
                MemoryPolicyReason.LOW_CONFIDENCE_REJECTED,
                [],
            )
        if not consent_state.consent_to_practice_summary:
            return MemoryPolicyDecision(
                proposal_id=proposal.proposal_id,
                action=MemoryPolicyAction.REQUIRE_CONFIRMATION,
                reason=MemoryPolicyReason.GENERAL_CONSENT_REQUIRED,
                safe_summary=redacted,
            )
        if proposal.memory_type in {
            MemoryType.SOCIAL_CONTEXT,
            MemoryType.RECURRING_PATTERN,
        }:
            return MemoryPolicyDecision(
                proposal_id=proposal.proposal_id,
                action=MemoryPolicyAction.REQUIRE_CONFIRMATION,
                reason=MemoryPolicyReason.SOCIAL_CONTEXT_CONFIRMATION_REQUIRED,
                safe_summary=redacted,
            )
        if (
            proposal.evidence_type == MemoryEvidenceType.COMPLETED_PRODUCT_ACTION
            and proposal.memory_type
            in {
                MemoryType.PRACTICE_EXPERIENCE,
                MemoryType.PRACTICE_MILESTONE,
            }
        ):
            return MemoryPolicyDecision(
                proposal_id=proposal.proposal_id,
                action=MemoryPolicyAction.AUTO_COMMIT,
                reason=MemoryPolicyReason.COMPLETED_PRACTICE_ALLOWED,
                safe_summary=redacted,
            )
        if (
            proposal.memory_type == MemoryType.HELPFUL_STRATEGY
            and proposal.evidence_type
            in {
                MemoryEvidenceType.EXPLICIT_USER_STATEMENT,
                MemoryEvidenceType.USER_CONFIRMED,
            }
            and proposal.confidence >= 0.8
        ):
            return MemoryPolicyDecision(
                proposal_id=proposal.proposal_id,
                action=MemoryPolicyAction.AUTO_COMMIT,
                reason=MemoryPolicyReason.HELPFUL_STRATEGY_ALLOWED,
                safe_summary=redacted,
            )
        return MemoryPolicyDecision(
            proposal_id=proposal.proposal_id,
            action=MemoryPolicyAction.REQUIRE_CONFIRMATION,
            reason=MemoryPolicyReason.EXPLICIT_EXPERIENCE_CONFIRMATION_REQUIRED,
            safe_summary=redacted,
        )


def _reject(
    proposal: MemoryProposal,
    reason: MemoryPolicyReason,
    detected: list[str],
) -> MemoryPolicyDecision:
    """Build a rejection that intentionally carries no candidate text."""
    return MemoryPolicyDecision(
        proposal_id=proposal.proposal_id,
        action=MemoryPolicyAction.REJECT,
        reason=reason,
        safe_summary=None,
        detected_categories=detected,
    )


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)
