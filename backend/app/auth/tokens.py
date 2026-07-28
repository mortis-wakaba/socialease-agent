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


JWT_ALGORITHM = "HS256"
TOKEN_USE = "access"
MIN_PRODUCTION_SECRET_BYTES = 32


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


def auth_signing_keys() -> dict[str, str]:
    """Return the configured versioned signing-key ring."""
    raw = os.getenv("SOCIALEASE_AUTH_TOKEN_KEYS", "").strip()
    if not raw:
        secret = auth_token_secret()
        return {"legacy": secret} if secret else {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("SOCIALEASE_AUTH_TOKEN_KEYS must be a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("SOCIALEASE_AUTH_TOKEN_KEYS must be a JSON object.")
    return {
        str(key_id): str(secret)
        for key_id, secret in parsed.items()
        if str(key_id).strip() and str(secret)
    }


def active_auth_signing_key() -> tuple[str, str]:
    """Return the active key id and secret used for newly issued tokens."""
    keys = auth_signing_keys()
    key_id = os.getenv("SOCIALEASE_AUTH_TOKEN_ACTIVE_KID", "").strip()
    if not key_id and set(keys) == {"legacy"}:
        key_id = "legacy"
    if not key_id or key_id not in keys:
        raise RuntimeError(
            "SOCIALEASE_AUTH_TOKEN_ACTIVE_KID must select a configured signing key."
        )
    return key_id, keys[key_id]


def validate_auth_configuration() -> None:
    """Fail startup when production authentication is not safely configured."""
    mode = auth_mode()
    if mode not in {"demo", "production"}:
        raise RuntimeError(f"Unsupported SOCIALEASE_AUTH_MODE: {mode}")
    if mode != "production":
        return
    keys = auth_signing_keys()
    if not keys:
        raise RuntimeError(
            "A production authentication signing key is required."
        )
    if any(
        len(secret.encode("utf-8")) < MIN_PRODUCTION_SECRET_BYTES
        for secret in keys.values()
    ):
        raise RuntimeError(
            "Every authentication signing key must contain at least 32 bytes."
        )
    active_auth_signing_key()


def create_auth_token(
    *,
    user_id: str,
    secret: str,
    tenant_id: str | None = None,
    roles: tuple[str, ...] = ("user",),
    ttl_seconds: int = 60 * 60,
    token_id: str | None = None,
    key_id: str | None = None,
) -> str:
    """Create an HS256 JWT bearer token for tests or local production-mode demos."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": list(roles),
        "token_use": TOKEN_USE,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if token_id is not None:
        payload["jti"] = token_id
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    if key_id is not None:
        header["kid"] = key_id
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
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthTokenError("Invalid token format.")
    if secret is not None:
        signing_secret = secret
    else:
        header = _b64decode_json(parts[0])
        key_id = str(header.get("kid") or "")
        keys = auth_signing_keys()
        signing_secret = (
            keys.get(key_id)
            if key_id
            else keys.get("legacy") if set(keys) == {"legacy"} else None
        )
        if signing_secret is None:
            raise AuthTokenError("Unknown token signing key.")
    payload = _verify_jwt(parts, signing_secret)
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


def _verified_from_payload(payload: dict[str, Any]) -> VerifiedToken:
    """Build verified identity claims from a decoded payload."""
    if payload.get("token_use") != TOKEN_USE:
        raise AuthTokenError("Invalid token use.")
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
