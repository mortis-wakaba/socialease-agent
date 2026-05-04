"""FastAPI routes for local knowledge-base retrieval."""

from fastapi import APIRouter

from app.knowledge.service import KnowledgeService
from app.models_knowledge import KnowledgeQueryRequest, KnowledgeQueryResponse

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
knowledge_service = KnowledgeService()


@router.post("/query", response_model=KnowledgeQueryResponse)
async def query_knowledge(
    request: KnowledgeQueryRequest,
) -> KnowledgeQueryResponse:
    """Query one demo knowledge base and return cited snippets."""
    return knowledge_service.query(
        query=request.query,
        kb_type=request.kb_type,
    )

