"""FastAPI routes for role-play practice sessions."""

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    resolve_optional_user_id,
    resolve_request_user_id,
)
from app.models_roleplay import (
    RoleplayFeedbackRequest,
    RoleplayFeedbackResponse,
    RoleplayMessageRequest,
    RoleplayMessageResponse,
    RoleplayPauseRequest,
    RoleplayPauseResponse,
    RoleplayResumeRequest,
    RoleplayResumeResponse,
    RoleplaySessionListResponse,
    RoleplayStartRequest,
    RoleplayStartResponse,
)
from app.services.errors import ServiceNotFoundError, ServiceStateError
from app.services.roleplay_service import roleplay_service
from app.safety.actions import HarnessAction
from app.safety.direct_actions import (
    PROTOCOL_HEADER_NAME,
    consume_direct_action_consent,
    require_direct_action_consent,
)

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
    return roleplay_service.list_sessions(user_id=effective_user_id, limit=bounded_limit)


@router.get("/{session_id}", response_model=RoleplayStartResponse)
async def get_roleplay_session(
    session_id: str,
    user_id: str | None = None,
    current_user: AuthContext = Depends(get_current_user),
) -> RoleplayStartResponse:
    """Return an existing role-play session for frontend restoration."""
    try:
        effective_user_id = resolve_optional_user_id(user_id, current_user)
        return roleplay_service.get_session(session_id=session_id, user_id=effective_user_id)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Role-play session not found")


@router.post("/start", response_model=RoleplayStartResponse)
async def start_roleplay(
    request: RoleplayStartRequest,
    current_user: AuthContext = Depends(get_current_user),
    protocol_id: str | None = Header(default=None, alias=PROTOCOL_HEADER_NAME),
) -> RoleplayStartResponse:
    """Create a role-play session for one supported social scenario."""
    effective_request = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )
    consent = require_direct_action_consent(
        user_id=effective_request.user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        payload=effective_request,
        protocol_id=protocol_id,
    )
    response = roleplay_service.start_session(effective_request)
    consume_direct_action_consent(
        user_id=effective_request.user_id,
        consent=consent,
        result_summary="Started role-play session.",
    )
    return response


@router.post("/message", response_model=RoleplayMessageResponse)
async def send_roleplay_message(
    request: RoleplayMessageRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> RoleplayMessageResponse:
    """Append a user message and return the next role-play turn."""
    try:
        effective_request = request.model_copy(
            update={"user_id": resolve_request_user_id(request.user_id, current_user)}
        )
        return await roleplay_service.send_message(effective_request)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Role-play session not found")
    except ServiceStateError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.post("/pause", response_model=RoleplayPauseResponse)
async def pause_roleplay_session(
    request: RoleplayPauseRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> RoleplayPauseResponse:
    """Pause a role-play session and persist its lifecycle status."""
    try:
        effective_request = request.model_copy(
            update={"user_id": resolve_request_user_id(request.user_id, current_user)}
        )
        return roleplay_service.pause_session(effective_request)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Role-play session not found")
    except ServiceStateError as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.post("/resume", response_model=RoleplayResumeResponse)
async def resume_roleplay_session(
    request: RoleplayResumeRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> RoleplayResumeResponse:
    """Resume a paused role-play session for continued practice."""
    try:
        effective_request = request.model_copy(
            update={"user_id": resolve_request_user_id(request.user_id, current_user)}
        )
        return roleplay_service.resume_session(effective_request)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Role-play session not found")
    except ServiceStateError as error:
        raise HTTPException(status_code=409, detail=str(error))


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
        return roleplay_service.get_feedback(effective_request)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Role-play session not found")
    except ServiceStateError as error:
        raise HTTPException(status_code=409, detail=str(error))
