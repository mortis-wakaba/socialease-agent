"""Owner-scoped API routes for inspecting and controlling agent memory."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user, require_owner_path_user
from app.memory.long_term_repository import (
    InvalidMemoryTransitionError,
    MemoryConflictError,
    MemoryNotFoundError,
)
from app.models_memory_center import (
    MemoryCenterResponse,
    MemoryEditRequest,
    MemoryMutationResponse,
    MemoryProposalDecisionResponse,
    MemoryProposalListResponse,
    MemoryVersionRequest,
)
from app.models_memory import (
    AgentMemoryType,
    MemoryTypePersonalizationRequest,
    MemoryTypePersonalizationResponse,
)
from app.services.memory_center_service import memory_center_service


router = APIRouter(tags=["memory-center"])


@router.get(
    "/users/{user_id}/memories",
    response_model=MemoryCenterResponse,
)
async def get_memory_center(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryCenterResponse:
    """Return the authenticated owner's bounded Memory Center snapshot."""
    require_owner_path_user(user_id, current_user)
    return await memory_center_service.snapshot(user_id)


@router.get(
    "/users/{user_id}/memory-proposals",
    response_model=MemoryProposalListResponse,
)
async def list_memory_proposals(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryProposalListResponse:
    """Return pending candidates that still require an owner decision."""
    require_owner_path_user(user_id, current_user)
    return await memory_center_service.list_proposals(user_id)


@router.put(
    "/users/{user_id}/memory/personalization/{memory_type}",
    response_model=MemoryTypePersonalizationResponse,
)
async def update_memory_type_personalization(
    user_id: str,
    memory_type: AgentMemoryType,
    request: MemoryTypePersonalizationRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryTypePersonalizationResponse:
    """Enable or disable one category for future agent personalization."""
    require_owner_path_user(user_id, current_user)
    disabled = await memory_center_service.set_type_personalization(
        user_id=user_id,
        memory_type=memory_type,
        enabled=request.enabled,
    )
    return MemoryTypePersonalizationResponse(
        user_id=user_id,
        memory_type=memory_type,
        enabled=request.enabled,
        disabled_memory_types=disabled,
    )


@router.patch(
    "/users/{user_id}/memories/{memory_id}",
    response_model=MemoryMutationResponse,
)
async def edit_memory(
    user_id: str,
    memory_id: str,
    request: MemoryEditRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryMutationResponse:
    """Edit one memory through safety validation and optimistic locking."""
    require_owner_path_user(user_id, current_user)
    try:
        return await memory_center_service.edit(
            user_id=user_id,
            memory_id=memory_id,
            summary=request.summary,
            expected_version=request.expected_version,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (MemoryNotFoundError, MemoryConflictError) as error:
        raise _memory_http_error(error) from error


@router.post(
    "/users/{user_id}/memories/{memory_id}/archive",
    response_model=MemoryMutationResponse,
)
async def archive_memory(
    user_id: str,
    memory_id: str,
    request: MemoryVersionRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryMutationResponse:
    """Archive one owner-scoped episodic memory."""
    require_owner_path_user(user_id, current_user)
    try:
        return await memory_center_service.archive(
            user_id=user_id,
            memory_id=memory_id,
            expected_version=request.expected_version,
        )
    except (
        MemoryNotFoundError,
        MemoryConflictError,
        InvalidMemoryTransitionError,
    ) as error:
        raise _memory_http_error(error) from error


@router.post(
    "/users/{user_id}/memories/{memory_id}/restore",
    response_model=MemoryMutationResponse,
)
async def restore_memory(
    user_id: str,
    memory_id: str,
    request: MemoryVersionRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryMutationResponse:
    """Restore one archived or inactive memory."""
    require_owner_path_user(user_id, current_user)
    try:
        return await memory_center_service.restore(
            user_id=user_id,
            memory_id=memory_id,
            expected_version=request.expected_version,
        )
    except (
        MemoryNotFoundError,
        MemoryConflictError,
        InvalidMemoryTransitionError,
    ) as error:
        raise _memory_http_error(error) from error


@router.delete(
    "/users/{user_id}/memories/{memory_id}",
    response_model=MemoryMutationResponse,
)
async def delete_memory(
    user_id: str,
    memory_id: str,
    request: MemoryVersionRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryMutationResponse:
    """Physically delete one owner-scoped memory body."""
    require_owner_path_user(user_id, current_user)
    try:
        return await memory_center_service.delete(
            user_id=user_id,
            memory_id=memory_id,
            expected_version=request.expected_version,
        )
    except (MemoryNotFoundError, MemoryConflictError) as error:
        raise _memory_http_error(error) from error


@router.post(
    "/users/{user_id}/memory-proposals/{proposal_id}/confirm",
    response_model=MemoryProposalDecisionResponse,
)
async def confirm_memory_proposal(
    user_id: str,
    proposal_id: str,
    request: MemoryVersionRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryProposalDecisionResponse:
    """Confirm one pending candidate and erase the proposal body."""
    require_owner_path_user(user_id, current_user)
    try:
        return await memory_center_service.confirm_proposal(
            user_id=user_id,
            proposal_id=proposal_id,
            expected_version=request.expected_version,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (MemoryNotFoundError, MemoryConflictError) as error:
        raise _memory_http_error(error) from error


@router.post(
    "/users/{user_id}/memory-proposals/{proposal_id}/reject",
    response_model=MemoryProposalDecisionResponse,
)
async def reject_memory_proposal(
    user_id: str,
    proposal_id: str,
    request: MemoryVersionRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryProposalDecisionResponse:
    """Reject one candidate and erase its pending body."""
    require_owner_path_user(user_id, current_user)
    try:
        return await memory_center_service.reject_proposal(
            user_id=user_id,
            proposal_id=proposal_id,
            expected_version=request.expected_version,
        )
    except (MemoryNotFoundError, MemoryConflictError) as error:
        raise _memory_http_error(error) from error


def _memory_http_error(error: Exception) -> HTTPException:
    if isinstance(error, MemoryNotFoundError):
        return HTTPException(status_code=404, detail="Memory not found")
    if isinstance(error, InvalidMemoryTransitionError):
        return HTTPException(status_code=409, detail="Memory transition is not allowed")
    return HTTPException(
        status_code=409,
        detail="Memory changed; refresh before retrying",
    )
