"""FastAPI entrypoint for the SocialEase Agent backend."""

from contextlib import asynccontextmanager
import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth.csrf import CsrfProtectionMiddleware
from app.db.capabilities import validate_runtime_database_support
from app.memory.runtime_requirements import (
    task_state_runtime_report,
    validate_task_state_runtime,
)
from app.memory.session_context_store import SessionContextStoreUnavailable
from app.memory.task_state_store import TaskStateStoreUnavailable
from app.middleware import RequestIdMiddleware
from app.observability.readiness import readiness_snapshot
from app.observability.request_logging import StructuredRequestLoggingMiddleware
from app.rate_limit import RateLimitMiddleware
from app.request_context import REQUEST_ID_HEADER, get_request_id
from app.memory.session_context_settings import roleplay_session_context_settings

validate_runtime_database_support()
validate_task_state_runtime()

from app.api.routes import router as api_router
from app.api.conversations import (
    close_cached_conversation_service,
    conversation_service,
)
from app.services.roleplay_service import roleplay_service
from app.services.support_resource_service import support_resource_service
from app.services.worksheet_service import worksheet_service


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Release the shared async Redis client on application shutdown."""
    yield
    await roleplay_service.close()
    await worksheet_service.close()
    await support_resource_service.close()
    await close_cached_conversation_service()

app = FastAPI(
    title="SocialEase Agent API",
    description=(
        "A safe, controllable agent workflow for university social stress "
        "practice. This API is not a medical or diagnostic product."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(CsrfProtectionMiddleware)
app.add_middleware(StructuredRequestLoggingMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "SOCIALEASE_CORS_ORIGINS",
            "http://127.0.0.1:3000,http://localhost:3000",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Return API errors with request observability metadata."""
    request_id = getattr(request.state, "request_id", None) or get_request_id()
    headers = dict(exc.headers or {})
    if request_id is not None:
        headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "request_id": request_id,
            "error_category": "HTTP_ERROR",
        },
        headers=headers,
    )


@app.exception_handler(TaskStateStoreUnavailable)
@app.exception_handler(SessionContextStoreUnavailable)
async def task_state_unavailable_handler(
    request: Request,
    _exc: Exception,
) -> JSONResponse:
    """Report shared task-state outages without leaking connection details."""
    request_id = getattr(request.state, "request_id", None) or get_request_id()
    headers = {REQUEST_ID_HEADER: request_id} if request_id is not None else {}
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Short-lived task context is temporarily unavailable",
            "request_id": request_id,
            "error_category": "TASK_STATE_UNAVAILABLE",
        },
        headers=headers,
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return a lightweight health status for local development."""
    return {"status": "ok"}


@app.get("/ready")
async def readiness_check() -> JSONResponse:
    """Return deployment readiness checks without exposing secrets."""
    status_code, payload = readiness_snapshot()
    context_settings = roleplay_session_context_settings()
    task_state = task_state_runtime_report()
    component_health = {
        "roleplay": False,
        "worksheet": False,
        "support_search": False,
        "conversation_context": False,
    }
    if task_state.configured:
        results = await asyncio.gather(
            roleplay_service.context_health(),
            worksheet_service.context_health(),
            support_resource_service.context_health(),
            conversation_service().context_health(),
            return_exceptions=True,
        )
        component_health = {
            name: result is True
            for name, result in zip(component_health, results, strict=True)
        }
    redis_available = task_state.configured and all(component_health.values())
    task_state_ok = redis_available or not task_state.required
    payload["checks"]["task_state"] = {
        "ok": task_state_ok,
        "required": task_state.required,
        "configured": task_state.configured,
        "backend": "redis" if context_settings.redis_url else "disabled",
        "components": component_health,
    }
    if not task_state_ok:
        status_code = 503
        payload["status"] = "not_ready"
    return JSONResponse(status_code=status_code, content=payload)
