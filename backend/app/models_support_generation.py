"""Validated output models for grounded CBT-style support generation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PresentationConstraints(BaseModel):
    """Safe, machine-checkable presentation preferences for one response."""

    model_config = ConfigDict(extra="forbid")

    verbosity: Literal["brief", "normal"] = "normal"
    max_chars: int | None = Field(default=None, ge=10, le=1000)
    output_format: Literal["plain", "single_sentence", "steps"] = "plain"
    requested_language: Literal["zh", "en"] | None = None
    item_count: int | None = Field(default=None, ge=1, le=5)
    plain_language: bool = False


class PrivacyCandidate(BaseModel):
    """One exact sensitive span proposed for application-side validation."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=80)
    category: Literal[
        "email",
        "national_id",
        "phone",
        "wechat",
        "qq",
        "student_id",
        "address",
        "class_group",
        "organization",
        "person_name",
        "third_party_identity",
    ]


class SupportGeneration(BaseModel):
    """One bounded, non-medical support response proposed by an LLM."""

    model_config = ConfigDict(extra="forbid")

    response_mode: Literal["support_only", "micro_cbt", "direct_practice", "clarify"]
    acknowledgement: str | None = Field(default=None, max_length=240)
    situation_summary: str | None = Field(default=None, max_length=240)
    automatic_thought: str | None = Field(default=None, max_length=240)
    fact_prediction_distinction: str | None = Field(default=None, max_length=360)
    balanced_thought: str | None = Field(default=None, max_length=300)
    suggested_phrase: str | None = Field(default=None, max_length=240)
    practice_steps: list[str] = Field(default_factory=list, max_length=3)
    followup_question: str | None = Field(default=None, max_length=240)
    pause_supported: Literal[True] = True
    needs_real_support: bool = False
    real_support_note: str | None = Field(default=None, max_length=300)
    presentation_constraints: PresentationConstraints = Field(
        default_factory=PresentationConstraints
    )
    privacy_candidates: list[PrivacyCandidate] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "SupportGeneration":
        """Require only the fields needed by the selected response mode."""
        if self.response_mode in {"support_only", "micro_cbt"} and not self.acknowledgement:
            raise ValueError("Support modes require acknowledgement.")
        if self.response_mode == "micro_cbt" and not self.practice_steps:
            raise ValueError("micro_cbt requires at least one practice step.")
        if self.response_mode == "direct_practice" and not self.suggested_phrase:
            raise ValueError("direct_practice requires suggested_phrase.")
        if self.response_mode == "clarify" and not self.followup_question:
            raise ValueError("clarify requires followup_question.")
        if self.response_mode in {"direct_practice", "clarify"} and self.needs_real_support:
            raise ValueError("Real-support responses cannot use a minimal response mode.")
        return self
