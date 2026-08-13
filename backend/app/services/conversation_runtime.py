"""Application-owned lifecycle for the unified conversation service."""

from functools import lru_cache

from app.conversation.context_provider import ProtectedConversationProjection
from app.conversation.module_overlay_store import ProtectedModuleOverlay
from app.llm.factory import create_local_llm_client
from app.memory.redis_settings import redis_task_state_settings
from app.memory.runtime_requirements import task_state_runtime_report
from app.memory.task_state_store import RedisTaskStateStore
from app.services.conversation_service import ConversationService
from app.tracing.logger import trace_logger
from app.workflow.default_hooks import create_default_hooks
from app.workflow.engine import AgentHarness
from app.workflow.router import LlmIntentRouter, RuleBasedIntentRouter


@lru_cache(maxsize=1)
def conversation_service() -> ConversationService:
    """Return the process-wide unified conversation coordinator."""
    local_llm_client = create_local_llm_client()
    intent_router = (
        LlmIntentRouter(llm_client=local_llm_client)
        if local_llm_client is not None
        else RuleBasedIntentRouter()
    )
    return ConversationService(
        harness=AgentHarness(
            trace_logger=trace_logger,
            hooks=create_default_hooks(),
            intent_router=intent_router,
        ),
        intent_router=intent_router,
        lightweight_llm_client=local_llm_client,
    )


async def close_conversation_service() -> None:
    """Close runtime resources only when the service was instantiated."""
    if conversation_service.cache_info().currsize:
        await conversation_service().close()


async def delete_conversation_runtime_for_user(*, user_id: str) -> None:
    """Erase conversation Redis projections without requiring durable content keys."""
    if conversation_service.cache_info().currsize:
        await conversation_service().delete_user_runtime_contexts(user_id=user_id)
        return
    report = task_state_runtime_report()
    if report.redis_url is None:
        return
    settings = redis_task_state_settings()
    stores = (
        RedisTaskStateStore(
            redis_url=report.redis_url,
            namespace="conversation-context",
            model_type=ProtectedConversationProjection,
            socket_timeout_seconds=settings.socket_timeout_seconds,
        ),
        RedisTaskStateStore(
            redis_url=report.redis_url,
            namespace="module-overlay",
            model_type=ProtectedModuleOverlay,
            socket_timeout_seconds=settings.socket_timeout_seconds,
        ),
    )
    try:
        for store in stores:
            await store.delete_user(user_id=user_id)
    finally:
        for store in stores:
            await store.close()
