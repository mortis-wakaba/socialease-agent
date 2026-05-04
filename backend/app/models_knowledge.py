"""Pydantic models for local knowledge-base retrieval."""

from enum import Enum

from pydantic import BaseModel, Field


class KnowledgeBaseType(str, Enum):
    """Supported knowledge-base collections."""

    SOCIAL_SKILLS = "social_skills"
    SAFETY_POLICY = "safety_policy"


class KnowledgeQueryRequest(BaseModel):
    """Request body for querying a knowledge base."""

    query: str = Field(min_length=1)
    kb_type: KnowledgeBaseType


class Citation(BaseModel):
    """Citation returned from a markdown knowledge chunk."""

    title: str
    source: str
    snippet: str


class KnowledgeQueryResponse(BaseModel):
    """Response returned by the RAG MVP endpoint."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)


class KnowledgeDocument(BaseModel):
    """Loaded markdown document with frontmatter metadata."""

    title: str
    source: str
    doc_type: str
    path: str
    content: str


class KnowledgeChunk(BaseModel):
    """Chunk of a knowledge document used for keyword retrieval."""

    title: str
    source: str
    path: str
    text: str

