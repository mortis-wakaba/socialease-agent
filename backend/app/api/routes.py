"""FastAPI routes for the SocialEase Agent API."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import router as auth_router
from app.api.calendar import router as calendar_router
from app.api.conversations import router as conversations_router
from app.api.exposure import router as exposure_router
from app.api.harness import router as harness_router
from app.api.intervention_plans import router as intervention_plans_router
from app.api.knowledge import router as knowledge_router
from app.api.memory_center import router as memory_center_router
from app.api.memory_doctor import router as memory_doctor_router
from app.api.profile import router as profile_router
from app.api.protocols import router as protocols_router
from app.api.roleplay import router as roleplay_router
from app.api.worksheet import router as worksheet_router
from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_optional_current_user,
    hide_if_not_owner,
    require_developer_access,
)
from app.models import TraceRecord
from app.tracing.logger import trace_logger

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(calendar_router)
router.include_router(conversations_router)
router.include_router(exposure_router)
router.include_router(harness_router)
router.include_router(intervention_plans_router)
router.include_router(knowledge_router)
router.include_router(memory_center_router)
router.include_router(memory_doctor_router)
router.include_router(profile_router)
router.include_router(protocols_router)
router.include_router(roleplay_router)
router.include_router(worksheet_router)


@router.get("/runs/{run_id}", response_model=TraceRecord)
async def get_run(
    run_id: str,
    current_user: AuthContext = Depends(get_optional_current_user),
) -> TraceRecord:
    """Return the trace record for a previous workflow run."""
    require_developer_access(current_user)
    record = await trace_logger.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    hide_if_not_owner(record.user_id, current_user)
    return record
