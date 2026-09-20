"""FastAPI routes for unified user-owned conversations."""

import asyncio
from collections.abc import AsyncIterator
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    resolve_optional_user_id,
    resolve_request_user_id,
)
from app.conversation.repository import (
    ConversationCommandRecord,
    ConversationIdempotencyError,
)
from app.models_conversation import (
    Conversation,
    ConversationPage,
    ModuleProposal,
)
from app.models_conversation_api import (
    ConversationCreateRequest,
    ConversationCommandRecoveryRequest,
    ConversationCommandRecoveryResponse,
    ConversationDeleteRequest,
    ConversationDeleteResponse,
    ConversationDetailResponse,
    ConversationExportCollectionResponse,
    ConversationExportResponse,
    ConversationMessageRequest,
    ConversationMessageResponse,
    ConversationStreamChunkEvent,
    ConversationStreamCompletedEvent,
    ConversationStreamCompletedResult,
    ConversationStreamErrorEvent,
    ConversationStreamHeartbeatEvent,
    ConversationModuleDecisionRequest,
    ConversationModuleTerminateRequest,
    ConversationUpdateRequest,
    ModuleControlResponse,
)
from app.services.conversation_service import (
    ConversationCommandInProgressError,
    ConversationNoticeError,
    ConversationProposalError,
)
from app.services.conversation_runtime import conversation_service
from app.services.response_streaming import (
    conversation_sse_event,
    sentence_chunks,
    sentence_streaming_allowed,
)


router = APIRouter(prefix="/conversations", tags=["conversations"])
_STREAM_PRODUCERS: set[asyncio.Task[None]] = set()


@router.post("", response_model=Conversation)
async def create_conversation(
    request: ConversationCreateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> Conversation:
    """Create a durable conversation after the current history notice."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        return await conversation_service().create_conversation(
            user_id=user_id,
            title=request.title,
            history_notice_version=request.history_notice_version,
            history_notice_acknowledged=request.history_notice_acknowledged,
        )
    except ConversationNoticeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=ConversationPage)
async def list_conversations(
    user_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationPage:
    """List durable conversation history for its owner."""
    effective_user_id = resolve_optional_user_id(user_id, current_user)
    try:
        return await conversation_service().list_conversations(
            user_id=effective_user_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    user_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationDetailResponse:
    """Return one owner-scoped conversation and timeline page."""
    effective_user_id = resolve_optional_user_id(user_id, current_user)
    service = conversation_service()
    conversation = await service.get_conversation(
        conversation_id=conversation_id,
        user_id=effective_user_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return ConversationDetailResponse(
            conversation=conversation,
            events=await service.list_events(
                conversation_id=conversation_id,
                user_id=effective_user_id,
                cursor=cursor,
                limit=limit,
            ),
            active_module_stack=await service.list_module_stack(
                conversation_id=conversation_id,
                user_id=effective_user_id,
            ),
            pending_module_proposals=await service.list_pending_proposals(
                conversation_id=conversation_id,
                user_id=effective_user_id,
            ),
            intent_plan=await service.get_intent_plan(
                conversation_id=conversation_id,
                user_id=effective_user_id,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{conversation_id}", response_model=Conversation)
async def update_conversation(
    conversation_id: str,
    request: ConversationUpdateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> Conversation:
    """Rename, archive, or unarchive one owner conversation."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        updated = await conversation_service().update_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            expected_version=request.expected_version,
            title=request.title,
            status=request.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return updated


@router.get(
    "/{conversation_id}/export",
    response_model=ConversationExportResponse,
)
async def export_conversation(
    conversation_id: str,
    user_id: str | None = None,
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationExportResponse:
    """Export one complete decrypted owner timeline."""
    effective_user_id = resolve_optional_user_id(user_id, current_user)
    exported = await conversation_service().export_conversation(
        conversation_id=conversation_id,
        user_id=effective_user_id,
    )
    if exported is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return exported


@router.get(
    "/all/export",
    response_model=ConversationExportCollectionResponse,
)
async def export_all_conversations(
    user_id: str | None = None,
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationExportCollectionResponse:
    """Export all complete timelines owned by the current user."""
    effective_user_id = resolve_optional_user_id(user_id, current_user)
    return await conversation_service().export_all_conversations(
        user_id=effective_user_id
    )


@router.delete(
    "/{conversation_id}",
    response_model=ConversationDeleteResponse,
)
async def delete_conversation(
    conversation_id: str,
    request: ConversationDeleteRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationDeleteResponse:
    """Permanently delete one conversation after explicit confirmation."""
    if not request.confirm_delete:
        raise HTTPException(status_code=409, detail="Delete confirmation required")
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        return await conversation_service().delete_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.delete("", response_model=ConversationDeleteResponse)
async def delete_all_conversations(
    request: ConversationDeleteRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationDeleteResponse:
    """Permanently delete every owner conversation after confirmation."""
    if not request.confirm_delete:
        raise HTTPException(status_code=409, detail="Delete confirmation required")
    user_id = resolve_request_user_id(request.user_id, current_user)
    return await conversation_service().delete_all_conversations(user_id=user_id)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationMessageResponse,
)
async def send_conversation_message(
    conversation_id: str,
    request: ConversationMessageRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationMessageResponse:
    """Append one message without allowing the model to start a module."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        async with asyncio.timeout(_conversation_turn_timeout_seconds()):
            return await conversation_service().send_message(
                conversation_id=conversation_id,
                user_id=user_id,
                message=request.message,
                idempotency_key=request.idempotency_key,
            )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Conversation turn timed out") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except (ConversationCommandInProgressError, ConversationIdempotencyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{conversation_id}/messages/stream")
async def stream_conversation_message(
    conversation_id: str,
    request: ConversationMessageRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> StreamingResponse:
    """Deliver eligible low-risk replies as policy-approved sentence buffers."""
    user_id = resolve_request_user_id(request.user_id, current_user)

    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        async def chunk_sink(text: str) -> None:
            await queue.put(("chunk", text))

        async def produce() -> None:
            try:
                async with asyncio.timeout(_conversation_turn_timeout_seconds()):
                    result = await conversation_service().send_message(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        message=request.message,
                        idempotency_key=request.idempotency_key,
                        streaming_requested=True,
                        response_chunk_sink=chunk_sink,
                    )
                await queue.put(("completed", result))
            except TimeoutError:
                await queue.put(("error", "The response timed out; retry this message"))
            except LookupError:
                await queue.put(("error", "Conversation not found"))
            except (
                ConversationCommandInProgressError,
                ConversationIdempotencyError,
            ) as exc:
                await queue.put(("error", str(exc)))
            except Exception:
                await queue.put(
                    ("error", "The response could not be completed")
                )

        producer = asyncio.create_task(produce())
        _STREAM_PRODUCERS.add(producer)
        producer.add_done_callback(_STREAM_PRODUCERS.discard)
        streamed_chunks = 0
        while True:
            try:
                kind, payload = await asyncio.wait_for(
                    queue.get(),
                    timeout=_conversation_heartbeat_seconds(),
                )
            except TimeoutError:
                yield conversation_sse_event(ConversationStreamHeartbeatEvent())
                continue
            if kind == "chunk" and isinstance(payload, str):
                streamed_chunks += 1
                yield conversation_sse_event(
                    ConversationStreamChunkEvent(text=payload)
                )
                continue
            if kind == "completed" and isinstance(
                payload, ConversationMessageResponse
            ):
                if streamed_chunks == 0 and sentence_streaming_allowed(payload):
                    for chunk in sentence_chunks(payload.response):
                        yield conversation_sse_event(
                            ConversationStreamChunkEvent(text=chunk)
                        )
                yield conversation_sse_event(
                    ConversationStreamCompletedEvent(
                        result=_stream_completed_result(payload)
                    )
                )
                break
            if kind == "error" and isinstance(payload, str):
                yield conversation_sse_event(
                    ConversationStreamErrorEvent(detail=payload)
                )
                break

        # The module-level task set keeps a disconnected client's bounded producer
        # alive through durable command completion.

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{conversation_id}/commands/{idempotency_key}",
    response_model=ConversationCommandRecoveryResponse,
)
async def get_conversation_command(
    conversation_id: str,
    idempotency_key: str,
    user_id: str | None = None,
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationCommandRecoveryResponse:
    """Recover completed output or inspect an owner-scoped pending command."""
    effective_user_id = resolve_optional_user_id(user_id, current_user)
    service = conversation_service()
    record = await service.get_command(
        conversation_id=conversation_id,
        user_id=effective_user_id,
        idempotency_key=idempotency_key,
    )
    if record is None:
        conversation = await service.get_conversation(
            conversation_id=conversation_id,
            user_id=effective_user_id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return ConversationCommandRecoveryResponse(
            status="not_found",
            can_retry_same_request=True,
            can_abandon=False,
        )
    return _command_recovery_response(record)


@router.post(
    "/{conversation_id}/commands/{idempotency_key}/abandon",
    response_model=ConversationCommandRecoveryResponse,
)
async def abandon_conversation_command(
    conversation_id: str,
    idempotency_key: str,
    request: ConversationCommandRecoveryRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ConversationCommandRecoveryResponse:
    """Abandon an expired command without deleting its audit record."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    record = await conversation_service().abandon_command(
        conversation_id=conversation_id,
        user_id=user_id,
        idempotency_key=idempotency_key,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation command not found")
    if record.status in {"processing", "recoverable"}:
        raise HTTPException(status_code=409, detail="Conversation command is still processing")
    return _command_recovery_response(record)


def _stream_completed_result(
    response: ConversationMessageResponse,
) -> ConversationStreamCompletedResult:
    """Project a service response without presenting temporary text as history."""
    response_is_persisted = any(
        event.event_type.value == "crisis_escalated"
        and event.content == response.response
        for event in response.appended_events
    )
    transient_response = (
        response.response
        if response.crisis_persistence_status == "degraded_retry_required"
        and not response_is_persisted
        else None
    )
    return ConversationStreamCompletedResult(
        conversation=response.conversation,
        appended_events=response.appended_events,
        active_module_stack=response.active_module_stack,
        pending_module_proposal=response.pending_module_proposal,
        transient_response=transient_response,
        crisis_persistence_status=response.crisis_persistence_status,
    )


def _command_recovery_response(
    record: ConversationCommandRecord,
) -> ConversationCommandRecoveryResponse:
    """Convert a repository command record into its public recovery contract."""
    result = None
    if record.completed_result is not None:
        response = ConversationMessageResponse.model_validate_json(
            record.completed_result
        )
        result = _stream_completed_result(response)
    return ConversationCommandRecoveryResponse(
        status=record.status,
        result=result,
        can_retry_same_request=record.status == "recoverable",
        can_abandon=(
            record.status == "recoverable"
            and record.safety_recovery_status in {"not_required", "completed"}
        ),
        safety_recovery_status=record.safety_recovery_status,
    )


def _conversation_turn_timeout_seconds() -> float:
    """Return the bounded end-to-end deadline for one streamed turn."""
    return _bounded_float_env(
        "SOCIALEASE_CONVERSATION_TURN_TIMEOUT_SECONDS",
        default=85.0,
        minimum=10.0,
        maximum=300.0,
    )


def _conversation_heartbeat_seconds() -> float:
    """Return the idle interval between content-free SSE keepalives."""
    return _bounded_float_env(
        "SOCIALEASE_CONVERSATION_HEARTBEAT_SECONDS",
        default=15.0,
        minimum=1.0,
        maximum=30.0,
    )


def _bounded_float_env(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Parse and clamp one operational duration setting."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


@router.post(
    "/{conversation_id}/module-proposals/{proposal_id}/accept",
    response_model=ModuleControlResponse,
)
async def accept_module_proposal(
    conversation_id: str,
    proposal_id: str,
    request: ConversationModuleDecisionRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ModuleControlResponse:
    """Start a module only after an explicit owner confirmation."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        return await conversation_service().accept_proposal(
            conversation_id=conversation_id,
            proposal_id=proposal_id,
            user_id=user_id,
            request_hash=request.request_hash,
        )
    except (ConversationProposalError, ConversationCommandInProgressError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.post(
    "/{conversation_id}/module-proposals/{proposal_id}/reject",
    response_model=ModuleProposal,
)
async def reject_module_proposal(
    conversation_id: str,
    proposal_id: str,
    request: ConversationModuleDecisionRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ModuleProposal:
    """Reject a pending proposal without changing the module stack."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        return await conversation_service().reject_proposal(
            conversation_id=conversation_id,
            proposal_id=proposal_id,
            user_id=user_id,
            request_hash=request.request_hash,
        )
    except (ConversationProposalError, ConversationCommandInProgressError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{conversation_id}/modules/{module_run_id}/terminate",
    response_model=ModuleControlResponse,
)
async def terminate_current_module(
    conversation_id: str,
    module_run_id: str,
    request: ConversationModuleTerminateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ModuleControlResponse:
    """Terminate the active top module and resume its parent."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        return await conversation_service().terminate_current_module(
            conversation_id=conversation_id,
            module_run_id=module_run_id,
            user_id=user_id,
        )
    except ConversationCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Active module not found") from exc


@router.post(
    "/{conversation_id}/modules/terminate-all",
    response_model=ModuleControlResponse,
)
async def terminate_all_modules(
    conversation_id: str,
    request: ConversationModuleTerminateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> ModuleControlResponse:
    """Terminate every active frame and return to ordinary conversation."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        return await conversation_service().terminate_all_modules(
            conversation_id=conversation_id,
            user_id=user_id,
        )
    except ConversationCommandInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
