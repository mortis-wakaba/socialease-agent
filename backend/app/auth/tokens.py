"""JWT bearer token helpers for production auth MVP."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


TOKEN_PREFIX = "socialease.v1"
JWT_ALGORITHM = "HS256"


class AuthTokenError(ValueError):
    """Raised when a bearer token is invalid or expired."""


@dataclass(frozen=True)
class VerifiedToken:
    """Verified identity claims from a bearer token."""

    user_id: str
    tenant_id: str | None
    roles: tuple[str, ...]
    token_id: str | None = None


def auth_mode() -> str:
    """Return the configured authentication mode."""
    return os.getenv("SOCIALEASE_AUTH_MODE", "demo").strip().lower() or "demo"


def auth_token_secret() -> str | None:
    """Return the configured bearer-token signing secret."""
    secret = os.getenv("SOCIALEASE_AUTH_TOKEN_SECRET", "").strip()
    return secret or None


def create_auth_token(
    *,
    user_id: str,
    secret: str,
    tenant_id: str | None = None,
    roles: tuple[str, ...] = ("user",),
    ttl_seconds: int = 60 * 60,
    token_id: str | None = None,
) -> str:
    """Create an HS256 JWT bearer token for tests or local production-mode demos."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": list(roles),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if token_id is not None:
        payload["jti"] = token_id
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_b64 = _b64encode_json(header)
    payload_b64 = _b64encode_json(payload)
    signing_input = f"{header_b64}.{payload_b64}"
    signature = _sign(signing_input, secret)
    return f"{signing_input}.{signature}"


def create_token_id() -> str:
    """Return an opaque token id for revocable account-session tokens."""
    return uuid4().hex


def verify_auth_token(token: str, *, secret: str | None = None) -> VerifiedToken:
    """Verify a signed bearer token and return identity claims."""
    signing_secret = secret or auth_token_secret()
    if signing_secret is None:
        raise AuthTokenError("Auth token secret is not configured.")
    parts = token.split(".")
    if len(parts) == 3:
        payload = _verify_jwt(parts, signing_secret)
    elif len(parts) == 4 and ".".join(parts[:2]) == TOKEN_PREFIX:
        payload = _verify_legacy_token(parts, signing_secret)
    else:
        raise AuthTokenError("Invalid token format.")
    return _verified_from_payload(payload)


def _verify_jwt(parts: list[str], secret: str) -> dict[str, Any]:
    """Verify an HS256 JWT and return its payload."""
    header_b64, payload_b64, signature = parts
    expected = _sign(f"{header_b64}.{payload_b64}", secret)
    if not hmac.compare_digest(signature, expected):
        raise AuthTokenError("Invalid token signature.")
    header = _b64decode_json(header_b64)
    if header.get("alg") != JWT_ALGORITHM or header.get("typ") != "JWT":
        raise AuthTokenError("Unsupported token header.")
    return _b64decode_json(payload_b64)


def _verify_legacy_token(parts: list[str], secret: str) -> dict[str, Any]:
    """Verify the pre-JWT SocialEase token format for compatibility."""
    payload_b64 = parts[2]
    signature = parts[3]
    expected = _sign(payload_b64, secret)
    if not hmac.compare_digest(signature, expected):
        raise AuthTokenError("Invalid token signature.")
    return _b64decode_json(payload_b64)


def _verified_from_payload(payload: dict[str, Any]) -> VerifiedToken:
    """Build verified identity claims from a decoded payload."""
    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        raise AuthTokenError("Token subject is required.")
    exp = int(payload.get("exp") or 0)
    if exp <= int(time.time()):
        raise AuthTokenError("Token has expired.")
    roles_value = payload.get("roles", ["user"])
    roles = tuple(str(role) for role in roles_value if str(role)) if isinstance(roles_value, list) else ("user",)
    tenant = payload.get("tenant_id")
    token_id = payload.get("jti")
    return VerifiedToken(
        user_id=user_id,
        tenant_id=str(tenant) if tenant else None,
        roles=roles or ("user",),
        token_id=str(token_id) if token_id else None,
    )


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64encode_bytes(digest)


def _b64encode_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64encode_bytes(raw)


def _b64encode_bytes(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode_json(value: str) -> dict[str, Any]:
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + padding)
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise AuthTokenError("Invalid token payload.") from exc
    if not isinstance(payload, dict):
        raise AuthTokenError("Invalid token payload.")
    return payload
