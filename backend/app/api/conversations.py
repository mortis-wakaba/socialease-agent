"""FastAPI routes for unified user-owned conversations."""

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    resolve_optional_user_id,
    resolve_request_user_id,
)
from app.models_conversation import (
    Conversation,
    ConversationPage,
    ModuleProposal,
)
from app.models_conversation_api import (
    ConversationCreateRequest,
    ConversationDeleteRequest,
    ConversationDeleteResponse,
    ConversationDetailResponse,
    ConversationExportCollectionResponse,
    ConversationExportResponse,
    ConversationMessageRequest,
    ConversationMessageResponse,
    ConversationModuleDecisionRequest,
    ConversationModuleTerminateRequest,
    ConversationUpdateRequest,
    LegacyRoleplayImportResponse,
    ModuleControlResponse,
)
from app.services.conversation_service import (
    ConversationNoticeError,
    ConversationProposalError,
    ConversationService,
)
from app.services.roleplay_service import roleplay_service
from app.tracing.logger import trace_logger
from app.workflow.default_hooks import create_default_hooks
from app.workflow.engine import AgentHarness


router = APIRouter(prefix="/conversations", tags=["conversations"])


@lru_cache(maxsize=1)
def conversation_service() -> ConversationService:
    """Return the process-wide unified conversation coordinator."""
    return ConversationService(
        harness=AgentHarness(
            trace_logger=trace_logger,
            hooks=create_default_hooks(),
        )
    )


async def close_cached_conversation_service() -> None:
    """Close context-cache resources only when the service was instantiated."""
    if conversation_service.cache_info().currsize:
        await conversation_service().close()


@router.post("", response_model=Conversation)
async def create_conversation(
    request: ConversationCreateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> Conversation:
    """Create a durable conversation after the current history notice."""
    user_id = resolve_request_user_id(request.user_id, current_user)
    try:
        return conversation_service().create_conversation(
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
        return conversation_service().list_conversations(
            user_id=effective_user_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/imports/legacy-roleplay",
    response_model=LegacyRoleplayImportResponse,
)
async def import_legacy_roleplay(
    user_id: str | None = None,
    current_user: AuthContext = Depends(get_current_user),
) -> LegacyRoleplayImportResponse:
    """Idempotently expose legacy role-play sessions as archived timelines."""
    effective_user_id = resolve_optional_user_id(user_id, current_user)
    batch_size = 200
    offset = 0
    scanned_count = 0
    imported_count = 0
    try:
        while True:
            legacy = roleplay_service.list_sessions(
                user_id=effective_user_id,
                limit=batch_size,
                offset=offset,
            )
            result = conversation_service().import_legacy_roleplay_sessions(
                user_id=effective_user_id,
                sessions=legacy.sessions,
            )
            scanned_count += result.scanned_count
            imported_count += result.imported_count
            if len(legacy.sessions) < batch_size:
                break
            offset += batch_size
        return LegacyRoleplayImportResponse(
            user_id=effective_user_id,
            scanned_count=scanned_count,
            imported_count=imported_count,
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
    conversation = service.get_conversation(
        conversation_id=conversation_id,
        user_id=effective_user_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return ConversationDetailResponse(
            conversation=conversation,
            events=service.list_events(
                conversation_id=conversation_id,
                user_id=effective_user_id,
                cursor=cursor,
                limit=limit,
            ),
            active_module_stack=service.list_module_stack(
                conversation_id=conversation_id,
                user_id=effective_user_id,
            ),
            pending_module_proposals=service.list_pending_proposals(
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
        updated = conversation_service().update_conversation(
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
    exported = conversation_service().export_conversation(
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
    return conversation_service().export_all_conversations(
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
        return await conversation_service().send_message(
            conversation_id=conversation_id,
            user_id=user_id,
            message=request.message,
            idempotency_key=request.idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


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
    except ConversationProposalError as exc:
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
        return conversation_service().reject_proposal(
            conversation_id=conversation_id,
            proposal_id=proposal_id,
            user_id=user_id,
            request_hash=request.request_hash,
        )
    except ConversationProposalError as exc:
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
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
