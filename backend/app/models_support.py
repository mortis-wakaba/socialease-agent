"""Pydantic models for public support-resource navigation."""

from pydantic import BaseModel, Field

from app.models import SafetyResult
from app.models_knowledge import Citation


class SupportQueryRequest(BaseModel):
    """Request body for querying public support resources."""

    query: str = Field(min_length=1)


class SupportQueryResponse(BaseModel):
    """Response returned by the support-resource navigation endpoint."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)
    safety_result: SafetyResult
    blocked: bool = False
