"""FastAPI routes for public support-resource navigation."""

from fastapi import APIRouter, Depends

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user

from app.models_support import SupportQueryRequest, SupportQueryResponse
from app.services.support_resource_service import support_resource_service

router = APIRouter(prefix="/support", tags=["support"])


@router.post("/query", response_model=SupportQueryResponse)
async def query_support_resources(
    request: SupportQueryRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> SupportQueryResponse:
    """Query verified public support resources unless crisis escalation is required."""
    effective = request.model_copy(
        update={"user_id": current_user.user_id or request.user_id}
    )
    return await support_resource_service.query_resources(effective)
