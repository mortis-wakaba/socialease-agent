"""FastAPI routes for traceable intervention plan timelines."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user, require_owner_path_user
from app.models_intervention import InterventionPlanListResponse, InterventionPlanResponse
from app.services.intervention_plan_service import intervention_plan_service

router = APIRouter(tags=["intervention-plans"])


@router.get(
    "/intervention-plans/{plan_id}",
    response_model=InterventionPlanResponse,
)
async def get_intervention_plan(
    plan_id: str,
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> InterventionPlanResponse:
    """Return a display-friendly timeline for one intervention plan."""
    require_owner_path_user(user_id, current_user)
    plan = await intervention_plan_service.get_view_by_id(
        user_id=user_id,
        plan_id=plan_id,
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Intervention plan not found")
    return InterventionPlanResponse(plan=plan)


@router.post(
    "/intervention-plans/{plan_id}/pause",
    response_model=InterventionPlanResponse,
)
async def pause_intervention_plan(
    plan_id: str,
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> InterventionPlanResponse:
    """Pause an active intervention plan when the user wants to stop practice."""
    require_owner_path_user(user_id, current_user)
    plan = await intervention_plan_service.pause_plan(
        user_id=user_id,
        plan_id=plan_id,
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Intervention plan not found")
    view = await intervention_plan_service.get_view_by_id(
        user_id=user_id,
        plan_id=plan.plan_id,
    )
    if view is None:
        raise HTTPException(status_code=404, detail="Intervention plan not found")
    return InterventionPlanResponse(plan=view)


@router.get(
    "/users/{user_id}/intervention-plans",
    response_model=InterventionPlanListResponse,
)
async def list_user_intervention_plans(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
) -> InterventionPlanListResponse:
    """Return recent traceable intervention plans for one user."""
    require_owner_path_user(user_id, current_user)
    return InterventionPlanListResponse(
        user_id=user_id,
        plans=await intervention_plan_service.list_views_for_user(
            user_id=user_id,
            limit=limit,
        ),
    )
