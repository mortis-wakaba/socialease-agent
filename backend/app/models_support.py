"""Pydantic models for public support-resource navigation."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models import SafetyResult
from app.models_knowledge import Citation, RetrievalDiagnostics


class SupportQueryRequest(BaseModel):
    """Request body for querying public support resources."""

    query: str = Field(min_length=1)
    user_id: str | None = None
    search_session_id: str | None = None


class SupportQueryResponse(BaseModel):
    """Response returned by the support-resource navigation endpoint."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval: RetrievalDiagnostics | None = None
    safety_result: SafetyResult
    blocked: bool = False
    search_session_id: str | None = None
    resolved_reference_index: int | None = None


class SupportSearchContext(BaseModel):
    """TTL-bound retrieval state used for grounded follow-up references."""

    user_id: str
    search_session_id: str
    last_query: str
    recent_queries: list[str] = Field(default_factory=list, max_length=4)
    ordered_citations: list[Citation] = Field(default_factory=list, max_length=10)
    selected_citation_index: int | None = Field(default=None, ge=0)
    version: int = Field(default=1, ge=1)
    updated_at: datetime
