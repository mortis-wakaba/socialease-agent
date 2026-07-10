"""HttpOnly auth cookie helpers for the production auth MVP."""

from __future__ import annotations

import os
import secrets

from fastapi import Response

from app.services.account_service import ACCESS_TOKEN_TTL_SECONDS, REFRESH_TOKEN_TTL_DAYS


ACCESS_COOKIE_NAME = "socialease_access_token"
REFRESH_COOKIE_NAME = "socialease_refresh_token"
CSRF_COOKIE_NAME = "socialease_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def auth_cookies_enabled() -> bool:
    """Return whether auth endpoints should set HttpOnly token cookies."""
    return os.getenv("SOCIALEASE_AUTH_COOKIE_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cookie_secure() -> bool:
    """Return whether auth cookies require HTTPS transport."""
    return os.getenv("SOCIALEASE_AUTH_COOKIE_SECURE", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def set_auth_cookies(response: Response, *, access_token: str, refresh_token: str) -> None:
    """Set HttpOnly access and refresh token cookies."""
    if not auth_cookies_enabled():
        return
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        max_age=ACCESS_TOKEN_TTL_SECONDS,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        max_age=REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        secure=cookie_secure(),
        samesite="lax",
        max_age=REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    """Clear HttpOnly auth token cookies."""
    if not auth_cookies_enabled():
        return
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(
            name,
            httponly=True,
            secure=cookie_secure(),
            samesite="lax",
            path="/",
        )
