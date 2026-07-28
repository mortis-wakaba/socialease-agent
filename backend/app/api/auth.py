"""Authentication routes for real-user pilot accounts."""

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response, status

from app.auth.context import AuthContext
from app.auth.cookies import (
    REFRESH_COOKIE_NAME,
    auth_cookies_enabled,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.auth.csrf import origin_allowed_for_request
from app.auth.dependencies import get_current_user
from app.auth.dependencies import (
    developer_endpoints_enabled,
    get_optional_current_user,
)
from app.auth.rate_limit import check_auth_rate_limit
from app.auth.tokens import auth_mode
from app.models_auth import (
    AccountDeleteResponse,
    AuthConfigResponse,
    AuthMeResponse,
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    RegisterRequest,
)
from app.observability.runtime_events import record_memory_delete
from app.services.account_service import (
    AccountError,
    AccountLockedError,
    DuplicateAccountError,
    InvalidCredentialsError,
    SignupDisabledError,
    account_service,
    signup_enabled,
)
from app.services.conversation_runtime import delete_conversation_runtime_for_user
from app.services.support_resource_service import support_resource_service
from app.services.worksheet_service import worksheet_service


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config() -> AuthConfigResponse:
    """Return public auth flags so frontend UI matches backend policy."""
    return AuthConfigResponse(
        auth_mode=auth_mode(),
        signup_enabled=signup_enabled(),
        cookie_auth_enabled=auth_cookies_enabled(),
    )


@router.get("/me", response_model=AuthMeResponse)
async def auth_me(
    current_user: AuthContext = Depends(get_optional_current_user),
) -> AuthMeResponse:
    """Return the current non-sensitive auth state for frontend guards."""
    enabled = developer_endpoints_enabled()
    developer_access = enabled and (
        auth_mode() != "production"
        or bool({"developer", "admin"}.intersection(current_user.roles))
    )
    return AuthMeResponse(
        authenticated=current_user.has_authenticated_user,
        user_id=current_user.user_id,
        roles=list(current_user.roles),
        auth_mode=auth_mode(),
        is_demo_user=current_user.is_demo_user,
        developer_endpoints_enabled=enabled,
        developer_access=developer_access,
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    body: RegisterRequest,
    response: Response,
) -> AuthResponse:
    """Create an account and return access/refresh tokens."""
    _require_allowed_cookie_auth_origin(request)
    await check_auth_rate_limit(request, action="register", email=body.email)
    try:
        auth_response = await account_service.register(
            body.email,
            body.password,
            invite_code=body.invite_code,
        )
        set_auth_cookies(
            response,
            access_token=auth_response.tokens.access_token,
            refresh_token=auth_response.tokens.refresh_token,
        )
        return auth_response
    except SignupDisabledError as error:
        raise HTTPException(status_code=403, detail=str(error))
    except DuplicateAccountError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except AccountError as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/login", response_model=AuthResponse)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
) -> AuthResponse:
    """Authenticate an account and return access/refresh tokens."""
    _require_allowed_cookie_auth_origin(request)
    await check_auth_rate_limit(request, action="login", email=body.email)
    try:
        auth_response = await account_service.login(body.email, body.password)
        set_auth_cookies(
            response,
            access_token=auth_response.tokens.access_token,
            refresh_token=auth_response.tokens.refresh_token,
        )
        return auth_response
    except AccountLockedError as error:
        raise HTTPException(status_code=429, detail=str(error))
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=401, detail=str(error))
    except AccountError as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    response: Response,
    request: RefreshRequest | None = Body(default=None),
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> AuthResponse:
    """Rotate a refresh token and issue a fresh token pair."""
    refresh_token = _refresh_token_from_request(request, refresh_cookie)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token required.")
    try:
        auth_response = await account_service.refresh(refresh_token)
        set_auth_cookies(
            response,
            access_token=auth_response.tokens.access_token,
            refresh_token=auth_response.tokens.refresh_token,
        )
        return auth_response
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=401, detail=str(error))
    except AccountError as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    request: LogoutRequest | None = Body(default=None),
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> LogoutResponse:
    """Revoke the current refresh/access-token session."""
    refresh_token = _refresh_token_from_request(request, refresh_cookie)
    if not refresh_token:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh token required.")
    revoked = await account_service.logout(refresh_token)
    clear_auth_cookies(response)
    return LogoutResponse(revoked=revoked)


@router.delete("/account", response_model=AccountDeleteResponse)
async def delete_account(
    response: Response,
    current_user: AuthContext = Depends(get_current_user),
) -> AccountDeleteResponse:
    """Delete the authenticated account and its user-owned practice memory."""
    if not current_user.user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        result = await account_service.delete_account(current_user.user_id)
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except AccountError as error:
        raise HTTPException(status_code=500, detail=str(error))
    await worksheet_service.delete_user_context(current_user.user_id)
    await support_resource_service.delete_user_context(current_user.user_id)
    await delete_conversation_runtime_for_user(
        user_id=current_user.user_id
    )
    await record_memory_delete()
    clear_auth_cookies(response)
    return result


def _refresh_token_from_request(
    request: RefreshRequest | LogoutRequest | None,
    refresh_cookie: str | None,
) -> str | None:
    """Resolve refresh token from JSON body first, then HttpOnly cookie."""
    if request and request.refresh_token:
        return request.refresh_token
    return refresh_cookie


def _require_allowed_cookie_auth_origin(request: Request) -> None:
    """Require a trusted browser origin for cookie-backed login/register."""
    if auth_mode() != "production" or not auth_cookies_enabled():
        return
    if origin_allowed_for_request(request):
        return
    raise HTTPException(status_code=403, detail="Origin is not allowed for cookie auth.")
