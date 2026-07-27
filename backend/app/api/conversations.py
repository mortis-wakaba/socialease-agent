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
    ConversationDetailResponse,
    ConversationMessageRequest,
    ConversationMessageResponse,
    ConversationModuleDecisionRequest,
)
from app.services.conversation_service import (
    ConversationNoticeError,
    ConversationProposalError,
    ConversationService,
)
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
