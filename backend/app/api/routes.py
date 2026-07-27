"""FastAPI routes for the SocialEase Agent API."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

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
from app.workflow.events import WorkflowProgressEvent

router = APIRouter(prefix="/api")
workflow = AgentHarness(trace_logger=trace_logger, hooks=create_default_hooks())
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


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> StreamingResponse:
    """Stream privacy-safe workflow progress, then one fully guarded response."""
    effective_request = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        def publish_progress(event: WorkflowProgressEvent) -> None:
            queue.put_nowait(("progress", event.model_dump(mode="json")))

        async def run_workflow() -> None:
            try:
                result = await workflow.run(effective_request, event_sink=publish_progress)
            except asyncio.CancelledError:
                raise
            except Exception:
                queue.put_nowait(
                    (
                        "error",
                        {"message": "Agent workflow failed before producing a safe response."},
                    )
                )
            else:
                queue.put_nowait(("final", result.model_dump(mode="json")))
            finally:
                queue.put_nowait(("done", {}))

        task = asyncio.create_task(run_workflow())
        try:
            while True:
                event_name, payload = await queue.get()
                yield _format_sse(event_name, payload)
                if event_name == "done":
                    break
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event_name: str, payload: object) -> str:
    """Serialize one compact server-sent event."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"


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
