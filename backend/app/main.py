"""FastAPI entrypoint for the SocialEase Agent backend."""

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth.csrf import CsrfProtectionMiddleware
from app.db.capabilities import validate_runtime_database_support
from app.middleware import RequestIdMiddleware
from app.observability.readiness import readiness_snapshot
from app.observability.request_logging import StructuredRequestLoggingMiddleware
from app.rate_limit import RateLimitMiddleware
from app.request_context import REQUEST_ID_HEADER, get_request_id

validate_runtime_database_support()

from app.api.routes import router as api_router

app = FastAPI(
    title="SocialEase Agent API",
    description=(
        "A safe, controllable agent workflow for university social stress "
        "practice. This API is not a medical or diagnostic product."
    ),
    version="0.1.0",
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


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return a lightweight health status for local development."""
    return {"status": "ok"}


@app.get("/ready")
async def readiness_check() -> JSONResponse:
    """Return deployment readiness checks without exposing secrets."""
    status_code, payload = readiness_snapshot()
    return JSONResponse(status_code=status_code, content=payload)
