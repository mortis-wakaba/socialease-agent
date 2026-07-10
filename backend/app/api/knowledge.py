"""FastAPI routes for local knowledge-base retrieval."""

from fastapi import APIRouter, Depends

from app.auth.context import AuthContext
from app.auth.dependencies import get_optional_current_user, require_developer_access
from app.knowledge.service import KnowledgeService
from app.models_knowledge import KnowledgeBaseType
from app.models_knowledge import KnowledgeQueryRequest, KnowledgeQueryResponse

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
knowledge_service = KnowledgeService()
PUBLIC_KNOWLEDGE_BASES = {
    KnowledgeBaseType.SOCIAL_SKILLS,
    KnowledgeBaseType.SUPPORT_RESOURCES,
    KnowledgeBaseType.CAMPUS_RESOURCES_DEMO,
}


@router.post("/query", response_model=KnowledgeQueryResponse)
async def query_knowledge(
    request: KnowledgeQueryRequest,
    current_user: AuthContext = Depends(get_optional_current_user),
) -> KnowledgeQueryResponse:
    """Query one local knowledge base and return cited snippets."""
    if request.kb_type not in PUBLIC_KNOWLEDGE_BASES:
        require_developer_access(current_user)
    return knowledge_service.query(
        query=request.query,
        kb_type=request.kb_type,
    )
