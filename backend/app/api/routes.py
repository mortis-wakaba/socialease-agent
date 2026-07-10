"""FastAPI routes for the SocialEase Agent API."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import router as auth_router
from app.api.exposure import router as exposure_router
from app.api.harness import router as harness_router
from app.api.intervention_plans import router as intervention_plans_router
from app.api.knowledge import router as knowledge_router
from app.api.profile import router as profile_router
from app.api.protocols import router as protocols_router
from app.api.roleplay import router as roleplay_router
from app.api.support import router as support_router
from app.api.worksheet import router as worksheet_router
from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    get_optional_current_user,
    hide_if_not_owner,
    require_developer_access,
    resolve_request_user_id,
)
from app.models import ChatRequest, ChatResponse, TraceRecord
from app.tracing.logger import trace_logger
from app.workflow.default_hooks import create_default_hooks
from app.workflow.engine import AgentHarness

router = APIRouter(prefix="/api")
workflow = AgentHarness(trace_logger=trace_logger, hooks=create_default_hooks())
router.include_router(auth_router)
router.include_router(exposure_router)
router.include_router(harness_router)
router.include_router(intervention_plans_router)
router.include_router(knowledge_router)
router.include_router(profile_router)
router.include_router(protocols_router)
router.include_router(roleplay_router)
router.include_router(support_router)
router.include_router(worksheet_router)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ChatResponse:
    """Run the safety, routing, agent, and trace workflow for one message."""
    effective_request = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )
    return await workflow.run(effective_request)


@router.get("/runs/{run_id}", response_model=TraceRecord)
async def get_run(
    run_id: str,
    current_user: AuthContext = Depends(get_optional_current_user),
) -> TraceRecord:
    """Return the trace record for a previous workflow run."""
    require_developer_access(current_user)
    record = trace_logger.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    hide_if_not_owner(record.user_id, current_user)
    return record
