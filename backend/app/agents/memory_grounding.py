"""Typed generation for explicit questions about selected practice memory."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.llm.base import BaseLLMClient, JSONModeLLMClient
from app.llm.prompts import (
    build_memory_grounding_system_prompt,
    build_memory_grounding_user_prompt,
)
from app.llm.retry import ProviderError
from app.models_memory_tool import (
    MEMORY_GROUNDING_OUTPUT_PARSER_VERSION,
    MemoryGroundingGeneration,
)
from app.models_llm import LLMUsage
from app.privacy.redaction import (
    redact_sensitive_identifiers,
    redact_validated_candidates,
)


class MemoryGroundingGenerationAgent:
    """Answer from bounded selected evidence without imposing the CBT schema."""

    def __init__(self, *, llm_client: BaseLLMClient | None) -> None:
        self._llm_client = llm_client

    async def respond(
        self,
        *,
        current_request: str,
        evidence: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Return a validated historical answer or a deterministic safe fallback."""
        bounded = evidence[:3]
        if self._llm_client is None:
            return _fallback("LLM_DISABLED", len(bounded), False)
        json_mode_used = (
            isinstance(self._llm_client, JSONModeLLMClient)
            and self._llm_client.json_mode_enabled
        )
        try:
            kwargs = {
                "system_prompt": build_memory_grounding_system_prompt(),
                "user_prompt": build_memory_grounding_user_prompt(
                    current_request=current_request,
                    evidence=bounded,
                ),
                "temperature": 0.0,
            }
            raw = (
                await self._llm_client.generate_json_text(**kwargs)
                if json_mode_used
                else await self._llm_client.generate_text(**kwargs)
            )
            proposal = _parse_generation(raw)
            if any(
                number < 1 or number > len(bounded)
                for number in proposal.evidence_numbers
            ):
                raise ValueError("memory grounding cited unavailable evidence")
            _validate_grounded_text(proposal.grounded_summary or "")
        except MemoryGroundingGuardrailError as error:
            return _fallback(
                "OUTPUT_GUARDRAIL",
                len(bounded),
                json_mode_used,
                issue=error.__class__.__name__,
                error_category=error.category,
            )
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            return _fallback(
                "SCHEMA_VALIDATION_ERROR",
                len(bounded),
                json_mode_used,
                issue=error.__class__.__name__,
                error_category="SCHEMA_VALIDATION_ERROR",
            )
        except Exception as error:
            category = (
                error.category.value
                if isinstance(error, ProviderError)
                else "TRANSIENT_PROVIDER_ERROR"
            )
            return _fallback(
                "PROVIDER_ERROR",
                len(bounded),
                json_mode_used,
                issue=error.__class__.__name__,
                error_category=category,
            )
        response = proposal.grounded_summary or (
            proposal.uncertainty_note or "现有记录不足以回答这个历史问题。"
        )
        if proposal.grounded_summary and proposal.uncertainty_note:
            response = f"{response}\n\n{proposal.uncertainty_note}"
        response, deterministic_categories = redact_sensitive_identifiers(response)
        response, semantic_categories = redact_validated_candidates(
            response,
            [
                (candidate.text, candidate.category)
                for candidate in proposal.privacy_candidates
            ],
        )
        return response, {
            "agent": "memory_grounding_generation_agent",
            "action": "typed_memory_grounding",
            "response_mode": proposal.response_mode,
            "pause_supported": proposal.pause_supported,
            "needs_real_support": proposal.needs_real_support,
            "memory_evidence_count": len(bounded),
            "cited_evidence_count": len(proposal.evidence_numbers),
            "json_mode_used": json_mode_used,
            "output_parser_version": MEMORY_GROUNDING_OUTPUT_PARSER_VERSION,
            "fallback_used": False,
            "llm_usage": LLMUsage(used=True).model_dump(mode="json"),
            "privacy_redaction": {
                "deterministic_categories": deterministic_categories,
                "semantic_categories": semantic_categories,
            },
            "safety_boundary": (
                "non_diagnostic_self_help_only; not a substitute for counseling"
            ),
        }


def _parse_generation(raw: str) -> MemoryGroundingGeneration:
    normalized = raw.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(?P<payload>.*?)\s*```",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced is not None:
        normalized = fenced.group("payload")
    payload = json.loads(normalized)
    if not isinstance(payload, dict):
        raise ValueError("memory grounding output must be an object")
    return MemoryGroundingGeneration.model_validate(payload)


class MemoryGroundingGuardrailError(ValueError):
    """Identify a content boundary separately from schema failures."""

    category = "PROHIBITED_SUPPORT_CONTENT"


def _validate_grounded_text(text: str) -> None:
    prohibited = (
        r"你(?:就是|已经|肯定)?患有",
        r"(?:确诊|诊断为).{0,12}(?:障碍|疾病|焦虑症|社交焦虑)",
        r"(?:一定|保证).{0,12}(?:治好|康复|有效|好起来)",
        r"只(?:需要)?和(?:我|系统|agent)聊",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
    )
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in prohibited):
        raise MemoryGroundingGuardrailError(
            "memory grounding failed output guardrails"
        )


def _fallback(
    reason: str,
    evidence_count: int,
    json_mode_used: bool,
    *,
    issue: str | None = None,
    error_category: str | None = None,
) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "agent": "memory_grounding_generation_agent",
        "action": "typed_memory_grounding",
        "fallback_used": True,
        "fallback_reason": reason,
        "validation_issues": [issue[:160]] if issue else [],
        "memory_evidence_count": evidence_count,
        "cited_evidence_count": 0,
        "json_mode_used": json_mode_used,
        "output_parser_version": MEMORY_GROUNDING_OUTPUT_PARSER_VERSION,
        "llm_usage": LLMUsage(
            used=reason != "LLM_DISABLED",
            fallback_used=reason != "LLM_DISABLED",
            error_category=error_category,
        ).model_dump(mode="json"),
        "safety_boundary": (
            "non_diagnostic_self_help_only; not a substitute for counseling"
        ),
    }
    return "现有记录不足以可靠回答这个历史问题。", metadata
