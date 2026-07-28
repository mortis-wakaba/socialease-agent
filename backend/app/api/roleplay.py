"""FastAPI routes for role-play practice sessions."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    resolve_optional_user_id,
    resolve_request_user_id,
)
from app.models_roleplay import (
    RoleplayFeedbackRequest,
    RoleplayFeedbackResponse,
    RoleplaySessionListResponse,
    RoleplayStartResponse,
)
from app.services.errors import ServiceNotFoundError, ServiceStateError
from app.services.roleplay_service import roleplay_service

router = APIRouter(prefix="/roleplay", tags=["roleplay"])


@router.get("", response_model=RoleplaySessionListResponse)
async def list_roleplay_sessions(
    user_id: str | None = None,
    limit: int = 20,
    current_user: AuthContext = Depends(get_current_user),
) -> RoleplaySessionListResponse:
    """Return recent role-play sessions for the current user."""
    effective_user_id = resolve_optional_user_id(user_id, current_user)
    bounded_limit = min(max(limit, 1), 50)
    return await roleplay_service.list_sessions(
        user_id=effective_user_id, limit=bounded_limit
    )


@router.get("/{session_id}", response_model=RoleplayStartResponse)
async def get_roleplay_session(
    session_id: str,
    user_id: str | None = None,
    current_user: AuthContext = Depends(get_current_user),
) -> RoleplayStartResponse:
    """Return an existing role-play session for frontend restoration."""
    try:
        effective_user_id = resolve_optional_user_id(user_id, current_user)
        return await roleplay_service.get_session(
            session_id=session_id, user_id=effective_user_id
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Role-play session not found")


@router.post("/feedback", response_model=RoleplayFeedbackResponse)
async def get_roleplay_feedback(
    request: RoleplayFeedbackRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> RoleplayFeedbackResponse:
    """Return structured feedback for a role-play session."""
    try:
        effective_request = request.model_copy(
            update={"user_id": resolve_request_user_id(request.user_id, current_user)}
        )
        return await roleplay_service.get_feedback(effective_request)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Role-play session not found")
    except ServiceStateError as error:
        raise HTTPException(status_code=409, detail=str(error))
