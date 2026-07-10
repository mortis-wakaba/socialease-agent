"""Pydantic models for real-user account authentication."""

from pydantic import BaseModel, Field


EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRequest(BaseModel):
    """Request body for creating a pilot user account."""

    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    invite_code: str | None = Field(default=None, min_length=1, max_length=128)


class LoginRequest(BaseModel):
    """Request body for signing into an existing account."""

    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """Request body for rotating a refresh token."""

    refresh_token: str | None = Field(default=None, min_length=32)


class LogoutRequest(BaseModel):
    """Request body for revoking a refresh/access-token session."""

    refresh_token: str | None = Field(default=None, min_length=32)


class AuthUser(BaseModel):
    """Public account identity returned to the frontend."""

    user_id: str
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)


class AuthTokenPair(BaseModel):
    """Access and refresh tokens for an authenticated account session."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthResponse(BaseModel):
    """Response returned after register/login/refresh."""

    user: AuthUser
    tokens: AuthTokenPair


class LogoutResponse(BaseModel):
    """Response returned after logout."""

    revoked: bool


class AccountDeleteResponse(BaseModel):
    """Response returned after deleting the current account."""

    deleted: bool
    revoked_sessions: int
    deleted_memory_counts: dict[str, int]


class AuthConfigResponse(BaseModel):
    """Public non-secret auth configuration for the frontend."""

    auth_mode: str
    signup_enabled: bool
    cookie_auth_enabled: bool


class AuthMeResponse(BaseModel):
    """Current non-sensitive authentication and role state."""

    authenticated: bool
    user_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    auth_mode: str
    is_demo_user: bool
    developer_endpoints_enabled: bool
    developer_access: bool
