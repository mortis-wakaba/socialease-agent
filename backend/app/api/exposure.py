"""FastAPI routes for graded exposure planning."""

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    resolve_optional_user_id,
    resolve_request_user_id,
    require_owner_path_user,
)
from app.models_exposure import (
    ExposureCompleteRequest,
    ExposureCompleteResponse,
    ExposurePlanRequest,
    ExposurePlanResponse,
    UserExposureResponse,
)
from app.services.errors import ServiceNotFoundError
from app.services.exposure_service import exposure_service
from app.safety.actions import HarnessAction
from app.safety.direct_actions import (
    PROTOCOL_HEADER_NAME,
    consume_direct_action_consent,
    require_direct_action_consent,
)

router = APIRouter(tags=["exposure"])


@router.post("/exposure/plan", response_model=ExposurePlanResponse)
async def create_exposure_plan(
    request: ExposurePlanRequest,
    current_user: AuthContext = Depends(get_current_user),
    protocol_id: str | None = Header(default=None, alias=PROTOCOL_HEADER_NAME),
) -> ExposurePlanResponse:
    """Create a graded exposure practice plan."""
    effective_request = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )
    consent = require_direct_action_consent(
        user_id=effective_request.user_id,
        harness_action=HarnessAction.CREATE_EXPOSURE_PLAN,
        payload=effective_request,
        protocol_id=protocol_id,
    )
    response = await exposure_service.create_plan(effective_request)
    if response.blocked is False:
        consume_direct_action_consent(
            user_id=effective_request.user_id,
            consent=consent,
            result_summary="Created exposure practice plan.",
        )
    return response


@router.post("/exposure/complete", response_model=ExposureCompleteResponse)
async def complete_exposure_task(
    request: ExposureCompleteRequest,
    current_user: AuthContext = Depends(get_current_user),
    protocol_id: str | None = Header(default=None, alias=PROTOCOL_HEADER_NAME),
) -> ExposureCompleteResponse:
    """Record task feedback and update the recommended next task."""
    try:
        effective_request = request.model_copy(
            update={"user_id": resolve_request_user_id(request.user_id, current_user)}
        )
        consent = require_direct_action_consent(
            user_id=effective_request.user_id,
            harness_action=HarnessAction.COMPLETE_EXPOSURE_TASK,
            payload=effective_request,
            protocol_id=protocol_id,
        )
        response = await exposure_service.complete_task(effective_request)
        if response.blocked is False:
            consume_direct_action_consent(
                user_id=effective_request.user_id,
                consent=consent,
                result_summary="Recorded exposure task feedback.",
            )
        return response
    except ServiceNotFoundError as error:
        detail = "Exposure task not found" if "task" in str(error).lower() else "Exposure plan not found"
        raise HTTPException(status_code=404, detail=detail)


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
