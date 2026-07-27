"""Pydantic models for public support-resource navigation."""

from datetime import datetime
from hashlib import sha256

from typing import Any

from pydantic import BaseModel, Field, model_validator

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
    """TTL-bound citation references; citation bodies stay in the knowledge base."""

    user_id: str
    search_session_id: str
    query_fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")
    ordered_citation_ids: list[str] = Field(default_factory=list, max_length=10)
    selected_citation_index: int | None = Field(default=None, ge=0)
    retrieval_unknown: bool = False
    version: int = Field(default=1, ge=1)
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def migrate_full_citation_cache(cls, data: Any) -> Any:
        """Read legacy Redis values once, then rewrite them as stable references."""
        if not isinstance(data, dict) or "ordered_citations" not in data:
            return data
        citations = [
            Citation.model_validate(item)
            for item in data.get("ordered_citations", [])
        ]
        return {
            "user_id": data["user_id"],
            "search_session_id": data["search_session_id"],
            "query_fingerprint": sha256(
                data.get("last_query", "").encode("utf-8")
            ).hexdigest()[:16],
            "ordered_citation_ids": [
                citation.citation_id for citation in citations
            ],
            "selected_citation_index": data.get("selected_citation_index"),
            "retrieval_unknown": not citations,
            "version": data.get("version", 1),
            "updated_at": data["updated_at"],
        }
