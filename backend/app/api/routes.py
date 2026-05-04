"""FastAPI routes for the SocialEase Agent MVP."""

from fastapi import APIRouter, HTTPException

from app.api.exposure import router as exposure_router
from app.api.knowledge import router as knowledge_router
from app.api.roleplay import router as roleplay_router
from app.api.worksheet import router as worksheet_router
from app.models import ChatRequest, ChatResponse, TraceRecord
from app.tracing.logger import trace_logger
from app.workflow.engine import AgentWorkflow

router = APIRouter(prefix="/api")
workflow = AgentWorkflow(trace_logger=trace_logger)
router.include_router(exposure_router)
router.include_router(knowledge_router)
router.include_router(roleplay_router)
router.include_router(worksheet_router)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Run the safety, routing, agent, and trace workflow for one message."""
    return await workflow.run(request)


@router.get("/runs/{run_id}", response_model=TraceRecord)
async def get_run(run_id: str) -> TraceRecord:
    """Return the trace record for a previous workflow run."""
    record = trace_logger.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record
