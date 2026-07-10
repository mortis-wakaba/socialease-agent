"""FastAPI routes for public support-resource navigation."""

from fastapi import APIRouter

from app.models_support import SupportQueryRequest, SupportQueryResponse
from app.services.support_resource_service import support_resource_service

router = APIRouter(prefix="/support", tags=["support"])


@router.post("/query", response_model=SupportQueryResponse)
async def query_support_resources(
    request: SupportQueryRequest,
) -> SupportQueryResponse:
    """Query verified public support resources unless crisis escalation is required."""
    return await support_resource_service.query_resources(request)
