"""FastAPI dependencies and guards for authentication."""

import os

from fastapi import Cookie, Header, HTTPException

from app.auth.context import AuthContext
from app.auth.cookies import ACCESS_COOKIE_NAME
from app.auth.tokens import AuthTokenError, auth_mode, verify_auth_token
from app.services.account_service import account_service


async def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    access_cookie: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
) -> AuthContext:
    """Resolve the current user from demo headers or production bearer tokens."""
    mode = auth_mode()
    if mode == "demo":
        user_id = x_demo_user_id.strip() if x_demo_user_id else None
        return AuthContext(
            user_id=user_id or None,
            roles=("demo_user",),
            is_demo_user=True,
        )
    if mode == "production":
        bearer_token = _bearer_token(authorization) if authorization else None
        token = bearer_token or access_cookie
        if not token:
            raise HTTPException(status_code=401, detail="Authentication required")
        if authorization and bearer_token is None:
            raise HTTPException(status_code=401, detail="Bearer token required")
        try:
            verified = verify_auth_token(token.strip())
        except AuthTokenError:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        if (
            verified.token_id is not None
            and not await account_service.is_access_token_active(verified.token_id)
        ):
            raise HTTPException(status_code=401, detail="Authentication token revoked")
        return AuthContext(
            user_id=verified.user_id,
            tenant_id=verified.tenant_id,
            roles=verified.roles,
            is_demo_user=False,
        )
    raise HTTPException(status_code=500, detail=f"Unsupported auth mode: {mode}")


async def get_optional_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    access_cookie: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
) -> AuthContext:
    """Resolve a user if provided, without requiring auth for public routes."""
    mode = auth_mode()
    has_token = bool(authorization or access_cookie)
    has_demo_user = bool(x_demo_user_id)
    if mode == "production" and not has_token:
        return AuthContext(
            user_id=None,
            roles=(),
            is_demo_user=False,
        )
    if mode == "demo" and not has_demo_user:
        return AuthContext(
            user_id=None,
            roles=("demo_user",),
            is_demo_user=True,
        )
    return await get_current_user(
        authorization=authorization,
        x_demo_user_id=x_demo_user_id,
        access_cookie=access_cookie,
    )


def _bearer_token(authorization: str | None) -> str | None:
    """Return the bearer token from an Authorization header, if valid."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def resolve_request_user_id(request_user_id: str, current_user: AuthContext) -> str:
    """Return the effective user_id for a write-style request."""
    return current_user.user_id or request_user_id


def resolve_optional_user_id(
    request_user_id: str | None,
    current_user: AuthContext,
) -> str:
    """Return an effective user_id when the route parameter is optional."""
    user_id = current_user.user_id or request_user_id
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required in demo mode")
    return user_id


def require_owner_path_user(user_id: str, current_user: AuthContext) -> str:
    """Require a path user_id to match the authenticated owner when present."""
    if current_user.user_id and user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="User does not own this resource")
    return user_id


def hide_if_not_owner(resource_user_id: str, current_user: AuthContext) -> None:
    """Hide resource existence when an authenticated user is not the owner."""
    if current_user.user_id and resource_user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Resource not found")


def developer_endpoints_enabled() -> bool:
    """Return whether developer-facing API surfaces are explicitly enabled."""
    return (
        os.getenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "").strip().lower()
        in {"1", "true", "yes"}
    )


def require_developer_access(current_user: AuthContext) -> None:
    """Require developer endpoint flag plus a developer/admin identity."""
    if not developer_endpoints_enabled():
        raise HTTPException(
            status_code=403,
            detail="Developer endpoints are disabled.",
        )
    if auth_mode() != "production":
        return
    allowed_roles = {"developer", "admin"}
    if allowed_roles.intersection(current_user.roles):
        return
    raise HTTPException(
        status_code=403,
        detail="Developer access is required.",
    )
