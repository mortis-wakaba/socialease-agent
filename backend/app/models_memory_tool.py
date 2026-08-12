"""Typed contracts for provider-native episodic memory Tools."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models_long_term_memory import MemoryType
from app.models_support_generation import PrivacyCandidate


MEMORY_TOOL_OUTPUT_PARSER_VERSION = "memory-tool-native-v1"
MEMORY_EVIDENCE_SELECTOR_OUTPUT_PARSER_VERSION = "memory-evidence-selector-json-v2"
MEMORY_TOOL_NATIVE_CALL_PROTOCOL_VERSION = "native-tools-v1"
MEMORY_GROUNDING_OUTPUT_PARSER_VERSION = "memory-grounding-json-v1"


class MemoryToolDecisionReason(str, Enum):
    """Bounded reason codes safe for traces and evaluation."""

    CURRENT_CONTEXT_SUFFICIENT = "current_context_sufficient"
    EXPLICIT_CROSS_SESSION_HISTORY = "explicit_cross_session_history"
    MEMORY_NOT_NEEDED = "memory_not_needed"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    EXPLICIT_MEMORY_CORRECTION = "explicit_memory_correction"


class MemoryEvidenceQuestionDirection(str, Enum):
    """Bounded semantic direction used by the set-level evidence selector."""

    HELPED = "helped"
    FAILED = "failed"
    EXACT_RECALL = "exact_recall"
    NEUTRAL_HISTORY = "neutral_history"
    CORRECTION = "correction"


class MemoryToolEvidence(BaseModel):
    """One bounded safe summary returned by the read-only memory tool."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^mem_ev_[0-9a-f]{16}$")
    memory_type: MemoryType
    safe_summary: str = Field(min_length=1, max_length=500)
    relevance_score: float = Field(ge=0.0, le=1.0)


class MemoryEvidenceSelection(BaseModel):
    """Strict subset selected from one bounded retrieval candidate set."""

    model_config = ConfigDict(extra="forbid")

    question_direction: MemoryEvidenceQuestionDirection
    selected_evidence_ids: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def require_unique_ids(self) -> "MemoryEvidenceSelection":
        """Reject duplicate evidence IDs instead of silently repairing output."""
        if len(self.selected_evidence_ids) != len(set(self.selected_evidence_ids)):
            raise ValueError("selected evidence ids must be unique")
        return self


class MemoryEvidenceSelectorResult(BaseModel):
    """Content-free selector outcome plus the approved evidence subset."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "not_run",
        "selected",
        "abstained",
        "invalid_model_output",
        "provider_error",
    ] = "not_run"
    candidate_count: int = Field(default=0, ge=0, le=3)
    question_direction: MemoryEvidenceQuestionDirection | None = None
    evidence: list[MemoryToolEvidence] = Field(default_factory=list, max_length=3)


class MemoryGroundingGeneration(BaseModel):
    """Structured direct answer to an explicit saved-history question."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    response_mode: Literal["historical_recall", "insufficient_evidence"]
    grounded_summary: str | None = Field(default=None, max_length=700)
    evidence_numbers: list[int] = Field(default_factory=list, max_length=3)
    uncertainty_note: str | None = Field(default=None, max_length=240)
    pause_supported: Literal[True] = True
    needs_real_support: Literal[False] = False
    privacy_candidates: list[PrivacyCandidate] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "MemoryGroundingGeneration":
        """Require explicit evidence citations for every historical claim."""
        if self.response_mode == "historical_recall":
            if not self.grounded_summary or not self.evidence_numbers:
                raise ValueError("historical recall requires summary and evidence numbers")
        elif self.grounded_summary is not None or self.evidence_numbers:
            raise ValueError("insufficient evidence cannot claim grounded content")
        if len(self.evidence_numbers) != len(set(self.evidence_numbers)):
            raise ValueError("evidence numbers must be unique")
        return self


class MemoryToolRunResult(BaseModel):
    """Content plus content-free diagnostics for one bounded tool decision."""

    model_config = ConfigDict(extra="forbid")

    decision_action: Literal[
        "skip",
        "search_practice_memory",
        "update_practice_memory",
    ] = "skip"
    decision_reason: MemoryToolDecisionReason = (
        MemoryToolDecisionReason.MEMORY_NOT_NEEDED
    )
    outcome: Literal[
        "skipped",
        "returned",
        "abstained",
        "blocked_control_intent",
        "blocked_risk",
        "invalid_model_output",
        "provider_error",
        "tool_error",
        "updated",
        "no_match",
        "ambiguous_target",
        "weak_target",
        "rejected",
        "concurrent_change",
    ] = "skipped"
    evidence: list[MemoryToolEvidence] = Field(default_factory=list, max_length=3)
    selector_outcome: Literal[
        "not_run",
        "selected",
        "abstained",
        "invalid_model_output",
        "provider_error",
    ] = "not_run"
    selector_candidate_count: int = Field(default=0, ge=0, le=3)
    selector_question_direction: MemoryEvidenceQuestionDirection | None = None
    call_protocol: Literal["native_tools_v1"] = "native_tools_v1"

    @property
    def prompt_evidence(self) -> list[str]:
        """Render bounded evidence for the existing generation prompt contract."""
        return [
            f"{item.memory_type.value}: {item.safe_summary}"
            for item in self.evidence
        ]

    def trace_metadata(self) -> dict[str, object]:
        """Return content-free fields safe for response metadata and traces."""
        return {
            "decision_action": self.decision_action,
            "decision_reason": self.decision_reason.value,
            "outcome": self.outcome,
            "evidence_count": len(self.evidence),
            "output_parser_version": MEMORY_TOOL_OUTPUT_PARSER_VERSION,
            "selector_outcome": self.selector_outcome,
            "selector_candidate_count": self.selector_candidate_count,
            "selector_question_direction": (
                self.selector_question_direction.value
                if self.selector_question_direction is not None
                else None
            ),
            "selector_output_parser_version": (
                MEMORY_EVIDENCE_SELECTOR_OUTPUT_PARSER_VERSION
            ),
            "call_protocol": self.call_protocol,
            "native_call_protocol_version": (
                MEMORY_TOOL_NATIVE_CALL_PROTOCOL_VERSION
            ),
        }
