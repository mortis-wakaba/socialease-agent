"""CSRF and Origin checks for cookie-authenticated write requests."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.cookies import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
    auth_cookies_enabled,
)
from app.auth.tokens import auth_mode
from app.request_context import REQUEST_ID_HEADER


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/config",
}


class CsrfProtectionMiddleware(BaseHTTPMiddleware):
    """Reject cross-site writes that rely on cookie authentication."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not _requires_csrf_check(request):
            return await call_next(request)
        if _csrf_token_matches(request) or origin_allowed_for_request(request):
            return await call_next(request)
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            REQUEST_ID_HEADER
        )
        return JSONResponse(
            status_code=403,
            content={
                "detail": "CSRF validation failed.",
                "request_id": request_id,
                "error_category": "CSRF_VALIDATION_FAILED",
            },
            headers={REQUEST_ID_HEADER: request_id} if request_id else None,
        )


def _requires_csrf_check(request: Request) -> bool:
    """Return whether this request is a cookie-authenticated write."""
    if request.method.upper() not in MUTATING_METHODS:
        return False
    if request.url.path in EXEMPT_PATHS:
        return False
    if auth_mode() != "production" or not auth_cookies_enabled():
        return False
    authorization = request.headers.get("Authorization", "")
    if authorization.strip().lower().startswith("bearer "):
        return False
    return bool(
        request.cookies.get(ACCESS_COOKIE_NAME)
        or request.cookies.get(REFRESH_COOKIE_NAME)
    )


def _csrf_token_matches(request: Request) -> bool:
    """Return whether the double-submit CSRF token matches."""
    header_token = request.headers.get(CSRF_HEADER_NAME, "").strip()
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "").strip()
    return bool(header_token and cookie_token and header_token == cookie_token)


def origin_allowed_for_request(request: Request) -> bool:
    """Return whether Origin or Referer matches configured frontend origins."""
    origin = request.headers.get("Origin") or _referer_origin(request)
    if not origin:
        return False
    return origin.rstrip("/") in _allowed_origins()


def _referer_origin(request: Request) -> str | None:
    """Extract origin from Referer header if present."""
    referer = request.headers.get("Referer", "").strip()
    if not referer:
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _allowed_origins() -> set[str]:
    """Return frontend origins accepted for cookie-authenticated writes."""
    raw = os.getenv(
        "SOCIALEASE_CORS_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000",
    )
    return {origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()}
