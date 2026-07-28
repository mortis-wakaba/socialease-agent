"""Consent guard for direct state-changing API actions."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from app.auth.tokens import auth_mode
from app.models_protocols import ProtocolStatus
from app.protocols.service import protocol_service
from app.safety.actions import HarnessAction


PROTOCOL_HEADER_NAME = "X-SocialEase-Protocol-Id"


@dataclass(frozen=True)
class DirectActionConsent:
    """Approved protocol details for one direct API action."""

    protocol_id: str
    request_hash: str
    harness_action: HarnessAction


def direct_action_consent_enforced() -> bool:
    """Return whether direct state-changing APIs must require consent protocols."""
    configured = os.getenv("SOCIALEASE_ENFORCE_DIRECT_ACTION_CONSENT", "").strip().lower()
    if configured in {"1", "true", "yes"}:
        return True
    if configured in {"0", "false", "no"}:
        return False
    return auth_mode() == "production"


async def require_direct_action_consent(
    *,
    user_id: str,
    harness_action: HarnessAction,
    payload: BaseModel | dict[str, Any],
    protocol_id: str | None,
) -> DirectActionConsent | None:
    """Require and validate a consent protocol for a direct API action."""
    if not direct_action_consent_enforced():
        return None
    request_hash = direct_action_request_hash(
        harness_action=harness_action,
        payload=payload,
    )
    if protocol_id is None:
        protocol = await protocol_service.create_consent_request(
            user_id=user_id,
            harness_action=harness_action,
            reason="Direct state-changing API action requires explicit consent.",
            required_protocol=f"{harness_action.value}_direct_api_consent",
            session_id=None,
            request_hash=request_hash,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "action": "consent_required",
                "consent_required": True,
                "protocol_id": protocol.protocol_id,
                "protocol_status": protocol.status.value,
                "protocol_expires_at": protocol.expires_at.isoformat()
                if protocol.expires_at
                else None,
                "protocol_request_hash": protocol.request_hash,
                "harness_action": harness_action.value,
            },
        )
    claimed = await protocol_service.claim_for_action(
        protocol_id=protocol_id,
        user_id=user_id,
        harness_action=harness_action,
        request_hash=request_hash,
        session_id=None,
    )
    if claimed is None:
        raise HTTPException(status_code=403, detail="Approved consent protocol is required")
    return DirectActionConsent(
        protocol_id=protocol_id,
        request_hash=request_hash,
        harness_action=harness_action,
    )


async def require_direct_action_approval(
    *,
    user_id: str,
    harness_action: HarnessAction,
    payload: BaseModel | dict[str, Any],
    protocol_id: str | None,
) -> DirectActionConsent | None:
    """Validate consent without consuming it; a transactional outbox must consume it."""
    if not direct_action_consent_enforced():
        return None
    request_hash = direct_action_request_hash(
        harness_action=harness_action,
        payload=payload,
    )
    if protocol_id is None:
        protocol = await protocol_service.create_consent_request(
            user_id=user_id,
            harness_action=harness_action,
            reason="Direct state-changing API action requires explicit consent.",
            required_protocol=f"{harness_action.value}_direct_api_consent",
            session_id=None,
            request_hash=request_hash,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "action": "consent_required",
                "consent_required": True,
                "protocol_id": protocol.protocol_id,
                "protocol_status": protocol.status.value,
                "protocol_expires_at": (
                    protocol.expires_at.isoformat()
                    if protocol.expires_at
                    else None
                ),
                "protocol_request_hash": protocol.request_hash,
                "harness_action": harness_action.value,
            },
        )
    approved = await protocol_service.is_approved_for_action(
        protocol_id=protocol_id,
        user_id=user_id,
        harness_action=harness_action,
        request_hash=request_hash,
        session_id=None,
    )
    if not approved:
        # A consumed protocol may already own the exact outbox job. The outbox
        # performs the authoritative locked validation in that replay path.
        record = await protocol_service.store.get_for_user(protocol_id, user_id)
        if (
            record is None
            or record.status is not ProtocolStatus.CONSUMED
            or record.harness_action != harness_action.value
            or record.request_hash != request_hash
        ):
            raise HTTPException(
                status_code=403,
                detail="Approved consent protocol is required",
            )
    return DirectActionConsent(
        protocol_id=protocol_id,
        request_hash=request_hash,
        harness_action=harness_action,
    )


def direct_action_request_hash(
    *,
    harness_action: HarnessAction,
    payload: BaseModel | dict[str, Any],
) -> str:
    """Return a stable request hash for direct API consent binding."""
    if isinstance(payload, BaseModel):
        normalized_payload = payload.model_dump(mode="json")
    else:
        normalized_payload = payload
    encoded = json.dumps(
        {
            "harness_action": harness_action.value,
            "payload": normalized_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
