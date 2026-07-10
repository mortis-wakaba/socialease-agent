"""FastAPI routes for consent protocol responses."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user, resolve_request_user_id
from app.models_protocols import ProtocolRespondRequest, ProtocolResponse
from app.protocols.service import protocol_service

router = APIRouter(prefix="/protocols", tags=["protocols"])


@router.post("/{protocol_id}/respond", response_model=ProtocolResponse)
async def respond_to_protocol(
    protocol_id: str,
    request: ProtocolRespondRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ProtocolResponse:
    """Approve or reject a pending consent protocol."""
    protocol = protocol_service.respond(
        protocol_id=protocol_id,
        user_id=resolve_request_user_id(request.user_id, current_user),
        approved=request.approved,
    )
    if protocol is None:
        raise HTTPException(status_code=404, detail="Protocol not found")
    return ProtocolResponse(protocol=protocol)
