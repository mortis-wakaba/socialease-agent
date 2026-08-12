"""Set-level selector for bounded episodic memory evidence."""

from __future__ import annotations

import json
import os
import re

from pydantic import ValidationError

from app.llm.base import BaseLLMClient
from app.llm.prompts import (
    build_memory_evidence_selector_system_prompt,
    build_memory_evidence_selector_user_prompt,
)
from app.models_memory_tool import (
    MemoryEvidenceSelection,
    MemoryEvidenceSelectorResult,
    MemoryToolEvidence,
)


class MemoryEvidenceSelector:
    """Ask the model once for a validated 0-3 subset and otherwise fail closed."""

    def __init__(self, *, llm_client: BaseLLMClient | None) -> None:
        self._llm_client = llm_client

    async def select(
        self,
        *,
        historical_question: str,
        current_request: str,
        candidates: list[MemoryToolEvidence],
    ) -> MemoryEvidenceSelectorResult:
        """Return candidates in retrieval order after one strict set decision."""
        bounded = candidates[:3]
        if not bounded:
            return MemoryEvidenceSelectorResult(outcome="abstained")
        if self._llm_client is None:
            return MemoryEvidenceSelectorResult(
                outcome="provider_error",
                candidate_count=len(bounded),
            )
        try:
            raw = await self._llm_client.generate_text(
                system_prompt=build_memory_evidence_selector_system_prompt(),
                user_prompt=build_memory_evidence_selector_user_prompt(
                    historical_question=historical_question,
                    current_request=current_request,
                    candidates=[
                        {
                            "evidence_id": item.evidence_id,
                            "memory_type": item.memory_type.value,
                            "safe_summary": item.safe_summary,
                        }
                        for item in bounded
                    ],
                ),
                temperature=0.0,
            )
            selection = _parse_selection(raw)
            allowed_ids = {item.evidence_id for item in bounded}
            if not set(selection.selected_evidence_ids).issubset(allowed_ids):
                raise ValueError("selector returned an unknown evidence id")
        except (ValueError, json.JSONDecodeError, ValidationError):
            return MemoryEvidenceSelectorResult(
                outcome="invalid_model_output",
                candidate_count=len(bounded),
            )
        except Exception:
            return MemoryEvidenceSelectorResult(
                outcome="provider_error",
                candidate_count=len(bounded),
            )
        selected_ids = set(selection.selected_evidence_ids)
        evidence = [item for item in bounded if item.evidence_id in selected_ids]
        return MemoryEvidenceSelectorResult(
            outcome="selected" if evidence else "abstained",
            candidate_count=len(bounded),
            question_direction=selection.question_direction,
            evidence=evidence,
        )


def memory_evidence_selector_enabled() -> bool:
    """Enable v2b by default while retaining an explicit operational kill switch."""
    configured = os.getenv("SOCIALEASE_MEMORY_EVIDENCE_SELECTOR_ENABLED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return True


def _parse_selection(raw: str) -> MemoryEvidenceSelection:
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
        raise ValueError("memory evidence selection must be an object")
    return MemoryEvidenceSelection.model_validate(payload)
