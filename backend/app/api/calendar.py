"""Owner-scoped APIs for consented Calendar MCP operations."""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user, resolve_request_user_id
from app.calendar.mcp_client import CalendarMCPError
from app.calendar.outbox import CalendarActionOutbox, CalendarActionType
from app.calendar.outbox_processor import (
    CalendarOutboxProcessor,
    CalendarOutboxUnavailable,
)
from app.calendar.provider import CalendarEventNotFoundError
from app.calendar.service import CalendarVerificationError, calendar_service
from app.models_calendar import (
    CalendarCreateRequest,
    CalendarDeleteRequest,
    CalendarEventListResponse,
    CalendarEventResponse,
    CalendarUpdateRequest,
)
from app.safety.actions import HarnessAction
from app.safety.direct_actions import (
    PROTOCOL_HEADER_NAME,
    direct_action_request_hash,
    require_direct_action_approval,
)


router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.post("/events", response_model=CalendarEventResponse)
async def create_calendar_event(
    request: CalendarCreateRequest,
    current_user: AuthContext = Depends(get_current_user),
    protocol_id: str | None = Header(default=None, alias=PROTOCOL_HEADER_NAME),
) -> CalendarEventResponse:
    """Create one event only after consent bound to the complete proposal."""
    effective = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )
    response = await _enqueue_and_process(
        user_id=effective.user_id,
        harness_action=HarnessAction.CREATE_CALENDAR_EVENT,
        consent_payload=effective,
        outbox_payload=effective.model_dump(mode="json"),
        protocol_id=protocol_id,
        action_type="create",
        idempotency_key=effective.idempotency_key,
    )
    return response


@router.get("/events", response_model=CalendarEventListResponse)
async def list_calendar_events(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> CalendarEventListResponse:
    """List only SocialEase-created events belonging to the authenticated user."""
    effective_user_id = resolve_request_user_id(user_id, current_user)
    try:
        events = await calendar_service.list_owned_events(user_id=effective_user_id)
    except CalendarMCPError as exc:
        raise HTTPException(status_code=503, detail="Calendar tool is unavailable") from exc
    return CalendarEventListResponse(events=events)


@router.get("/events/{calendar_action_id}", response_model=CalendarEventResponse)
async def get_calendar_event(
    calendar_action_id: str,
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> CalendarEventResponse:
    """Return one owner-scoped event without exposing other calendar content."""
    effective_user_id = resolve_request_user_id(user_id, current_user)
    try:
        return await calendar_service.get_event(
            user_id=effective_user_id,
            calendar_action_id=calendar_action_id,
        )
    except CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Calendar event not found") from exc
    except (CalendarMCPError, CalendarVerificationError) as exc:
        raise HTTPException(status_code=503, detail="Calendar tool is unavailable") from exc


@router.put("/events/{calendar_action_id}", response_model=CalendarEventResponse)
async def update_calendar_event(
    calendar_action_id: str,
    request: CalendarUpdateRequest,
    current_user: AuthContext = Depends(get_current_user),
    protocol_id: str | None = Header(default=None, alias=PROTOCOL_HEADER_NAME),
) -> CalendarEventResponse:
    """Update one owned event after consent bound to id and replacement proposal."""
    effective = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )
    consent_payload = {
        "calendar_action_id": calendar_action_id,
        **effective.model_dump(mode="json"),
    }
    return await _enqueue_and_process(
        user_id=effective.user_id,
        harness_action=HarnessAction.UPDATE_CALENDAR_EVENT,
        consent_payload=consent_payload,
        outbox_payload=consent_payload,
        protocol_id=protocol_id,
        action_type="update",
    )


@router.delete("/events/{calendar_action_id}", response_model=CalendarEventResponse)
async def delete_calendar_event(
    calendar_action_id: str,
    request: CalendarDeleteRequest,
    current_user: AuthContext = Depends(get_current_user),
    protocol_id: str | None = Header(default=None, alias=PROTOCOL_HEADER_NAME),
) -> CalendarEventResponse:
    """Cancel one owned event after an id-bound consent handshake."""
    effective = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )
    consent_payload = {
        "calendar_action_id": calendar_action_id,
        **effective.model_dump(mode="json"),
    }
    return await _enqueue_and_process(
        user_id=effective.user_id,
        harness_action=HarnessAction.DELETE_CALENDAR_EVENT,
        consent_payload=consent_payload,
        outbox_payload=consent_payload,
        protocol_id=protocol_id,
        action_type="delete",
    )


async def _enqueue_and_process(
    *,
    user_id: str,
    harness_action: HarnessAction,
    consent_payload: BaseModel | dict[str, Any],
    outbox_payload: dict[str, object],
    protocol_id: str | None,
    action_type: CalendarActionType,
    idempotency_key: str | None = None,
) -> CalendarEventResponse:
    """Atomically enqueue an approved action, then attempt low-latency execution."""
    consent = await require_direct_action_approval(
        user_id=user_id,
        harness_action=harness_action,
        payload=consent_payload,
        protocol_id=protocol_id,
    )
    request_hash = (
        consent.request_hash
        if consent is not None
        else direct_action_request_hash(
            harness_action=harness_action,
            payload=consent_payload,
        )
    )
    outbox = CalendarActionOutbox()
    job = await outbox.enqueue(
        protocol_id=consent.protocol_id if consent is not None else None,
        user_id=user_id,
        action_type=action_type,
        request_hash=request_hash,
        idempotency_key=idempotency_key or f"calendar-outbox:{request_hash}",
        payload=outbox_payload,
    )
    if job is None:
        raise HTTPException(
            status_code=403,
            detail="Approved consent protocol is required",
        )
    try:
        return await CalendarOutboxProcessor(outbox=outbox).process_job(job.job_id)
    except CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Calendar event not found") from exc
    except (
        CalendarMCPError,
        CalendarVerificationError,
        CalendarOutboxUnavailable,
    ) as exc:
        raise HTTPException(status_code=503, detail="Calendar tool is unavailable") from exc
