"""FastAPI routes for graded exposure planning."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    resolve_optional_user_id,
    require_owner_path_user,
)
from app.models_exposure import (
    UserExposureResponse,
)
from app.services.errors import ServiceNotFoundError
from app.services.exposure_service import exposure_service

router = APIRouter(tags=["exposure"])


@router.get("/exposure/{plan_id}", response_model=UserExposureResponse)
async def get_exposure_plan_by_id(
    plan_id: str,
    user_id: str | None = None,
    current_user: AuthContext = Depends(get_current_user),
) -> UserExposureResponse:
    """Return one exposure plan by id for the owning user."""
    try:
        effective_user_id = resolve_optional_user_id(user_id, current_user)
        return exposure_service.get_plan_by_id(plan_id=plan_id, user_id=effective_user_id)
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Exposure plan not found")


@router.get("/users/{user_id}/exposure", response_model=UserExposureResponse)
async def get_user_exposure(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> UserExposureResponse:
    """Return the user's active exposure plan and progress."""
    require_owner_path_user(user_id, current_user)
    return exposure_service.get_user_plan(user_id)
