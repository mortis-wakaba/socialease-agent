"""Typed contracts for natural-language memory correction resolution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models_long_term_memory import MemoryType


MEMORY_CORRECTION_OUTPUT_PARSER_VERSION = "memory-correction-json-v1"


class MemoryCorrectionExtraction(BaseModel):
    """Untrusted model proposal that deliberately cannot identify a record."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["skip", "propose"]
    target_query: str | None = Field(default=None, min_length=2, max_length=500)
    replacement_summary: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_action_fields(self) -> "MemoryCorrectionExtraction":
        """Require both text fields only for an explicit correction proposal."""
        if self.action == "propose":
            if self.target_query is None or self.replacement_summary is None:
                raise ValueError("correction proposal requires query and replacement")
        elif self.target_query is not None or self.replacement_summary is not None:
            raise ValueError("skipped correction cannot carry proposal fields")
        return self


class MemoryCorrectionAutoUpdateResult(BaseModel):
    """Content-bounded outcome of one conservative autonomous correction."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "updated",
        "not_correction",
        "no_match",
        "ambiguous_target",
        "weak_target",
        "rejected",
        "unavailable",
        "resolution_failed",
        "concurrent_change",
        "blocked_risk",
    ]
    memory_type: MemoryType | None = None
    output_parser_version: str = MEMORY_CORRECTION_OUTPUT_PARSER_VERSION
