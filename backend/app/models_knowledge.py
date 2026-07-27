"""Pydantic models for local knowledge-base retrieval."""

from enum import Enum
from hashlib import sha256

from typing import Any

from pydantic import BaseModel, Field, model_validator


class KnowledgeBaseType(str, Enum):
    """Supported knowledge-base collections."""

    SOCIAL_SKILLS = "social_skills"
    SUPPORT_RESOURCES = "support_resources"
    SAFETY_POLICY = "safety_policy"
    PRODUCT_RUBRICS = "product_rubrics"


class KnowledgeQueryRequest(BaseModel):
    """Request body for querying a knowledge base."""

    query: str = Field(min_length=1)
    kb_type: KnowledgeBaseType


class Citation(BaseModel):
    """Citation returned from a markdown knowledge chunk."""

    citation_id: str | None = Field(default=None, min_length=16, max_length=64)
    title: str
    source_name: str
    source_type: str
    source_url: str | None = None
    snippet: str

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_source(cls, data: Any) -> Any:
        """Accept persisted citations written before source fields were split."""
        if isinstance(data, dict) and "source_name" not in data and "source" in data:
            source = data["source"]
            return {
                **data,
                "source_name": source,
                "source_type": (
                    "project_authored"
                    if source == "Synthetic demo knowledge base"
                    else "unknown"
                ),
            }
        return data

    @model_validator(mode="after")
    def populate_legacy_citation_id(self) -> "Citation":
        """Give persisted legacy citations a deterministic content identifier."""
        if self.citation_id is not None:
            return self
        canonical = "\x1f".join(
            [
                self.title.strip(),
                self.source_name.strip(),
                self.source_type.strip(),
                (self.source_url or "").strip(),
                self.snippet.strip(),
            ]
        )
        object.__setattr__(
            self,
            "citation_id",
            sha256(canonical.encode("utf-8")).hexdigest()[:24],
        )
        return self


class RetrievalHit(BaseModel):
    """One retrieval hit used for debugging and eval diagnostics."""

    title: str
    score: float
    source_type: str


class RetrievalDiagnostics(BaseModel):
    """Retrieval metadata returned by the local RAG service."""

    retriever: str
    top_k: int
    hits: list[RetrievalHit] = Field(default_factory=list)


class KnowledgeQueryResponse(BaseModel):
    """Response returned by the RAG endpoint."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval: RetrievalDiagnostics | None = None


class KnowledgeDocument(BaseModel):
    """Loaded markdown document with frontmatter metadata."""

    title: str
    source_name: str
    source_type: str
    source_url: str | None = None
    doc_type: str
    kb_type: KnowledgeBaseType
    audience: str
    review_status: str
    last_reviewed: str | None = None
    path: str
    content: str


class KnowledgeChunk(BaseModel):
    """Chunk of a knowledge document used for keyword retrieval."""

    title: str
    source_name: str
    source_type: str
    source_url: str | None = None
    path: str
    text: str
