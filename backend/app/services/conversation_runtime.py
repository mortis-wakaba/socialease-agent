"""Application-owned lifecycle for the unified conversation service."""

from datetime import timedelta
from functools import lru_cache
import os

from app.conversation.adapters import (
    ExposureModuleAdapter,
    ResourceModuleAdapter,
    RoleplayModuleAdapter,
    WorksheetModuleAdapter,
)
from app.conversation.compactor import ConversationCompactor
from app.conversation.context_manager import ConversationContextManager
from app.conversation.context_provider import (
    ProtectedConversationProjection,
    create_conversation_context_provider,
)
from app.conversation.module_coordinator import ModuleCoordinator
from app.conversation.module_overlay_store import ProtectedModuleOverlay
from app.conversation.repository import ConversationRepository
from app.conversation.reversible_actions import ReversibleCalendarExecutor
from app.db.factory import repository_factory
from app.llm.base import BaseLLMClient
from app.llm.factory import create_local_llm_client
from app.memory.active_memory_assembler import ActiveMemoryAssembler
from app.memory.completed_event_writer import CompletedPracticeMemoryWriter
from app.memory.context_orchestrator import MemoryContextOrchestrator
from app.memory.redis_settings import redis_task_state_settings
from app.memory.runtime_requirements import task_state_runtime_report
from app.memory.task_state_store import RedisTaskStateStore
from app.memory.token_estimator import ConservativeTokenEstimator
from app.models_conversation import ModuleType
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.services.conversation_service import ConversationService
from app.tracing.logger import trace_logger
from app.workflow.default_hooks import create_default_hooks
from app.workflow.engine import AgentHarness
from app.workflow.router import (
    BaseIntentRouter,
    LlmIntentRouter,
    RuleBasedIntentRouter,
)


def build_conversation_service(
    *,
    harness: AgentHarness,
    repository: ConversationRepository | None = None,
    safety_classifier: BaseSafetyClassifier | None = None,
    intent_router: BaseIntentRouter | None = None,
    context_manager: ConversationContextManager | None = None,
    module_coordinator: ModuleCoordinator | None = None,
    memory_context_orchestrator: MemoryContextOrchestrator | None = None,
    lightweight_llm_client: BaseLLMClient | None = None,
    reversible_calendar_executor: ReversibleCalendarExecutor | None = None,
    proposal_ttl: timedelta = timedelta(minutes=15),
    command_lease_seconds: int = 90,
) -> ConversationService:
    """Compose the unified conversation application service."""
    factory = repository_factory()
    repository = repository or factory.conversation_repository()
    safety_classifier = safety_classifier or create_safety_classifier()
    if intent_router is None:
        intent_router = (
            LlmIntentRouter(llm_client=lightweight_llm_client)
            if lightweight_llm_client is not None
            else RuleBasedIntentRouter()
        )

    estimator = ConservativeTokenEstimator()
    memory_context_orchestrator = (
        memory_context_orchestrator
        or MemoryContextOrchestrator(
            user_profile_repository=factory.user_profile_repository(),
            settings_repository=factory.user_memory_settings_repository(),
            assembler=ActiveMemoryAssembler(token_estimator=estimator),
        )
    )
    context_manager = context_manager or ConversationContextManager(
        repository=repository,
        compactor=ConversationCompactor(
            llm_client=lightweight_llm_client,
            token_estimator=estimator,
        ),
        token_estimator=estimator,
        provider=create_conversation_context_provider(repository),
    )
    if module_coordinator is None:
        memory_writer = CompletedPracticeMemoryWriter(
            memory_repository=factory.long_term_memory_repository(),
            settings_repository=factory.user_memory_settings_repository(),
        )
        module_coordinator = ModuleCoordinator(
            repository=repository,
            adapters={
                ModuleType.ROLEPLAY: RoleplayModuleAdapter(
                    memory_writer=memory_writer
                ),
                ModuleType.WORKSHEET: WorksheetModuleAdapter(
                    memory_writer=memory_writer
                ),
                ModuleType.EXPOSURE: ExposureModuleAdapter(
                    memory_writer=memory_writer
                ),
                ModuleType.RESOURCE: ResourceModuleAdapter(),
            },
        )
    return ConversationService(
        harness=harness,
        repository=repository,
        safety_classifier=safety_classifier,
        intent_router=intent_router,
        context_manager=context_manager,
        module_coordinator=module_coordinator,
        memory_context_orchestrator=memory_context_orchestrator,
        reversible_calendar_executor=(
            reversible_calendar_executor or ReversibleCalendarExecutor()
        ),
        proposal_ttl=proposal_ttl,
        command_lease_seconds=command_lease_seconds,
    )


@lru_cache(maxsize=1)
def conversation_service() -> ConversationService:
    """Return the process-wide unified conversation coordinator."""
    local_llm_client = create_local_llm_client()
    intent_router = (
        LlmIntentRouter(llm_client=local_llm_client)
        if local_llm_client is not None
        else RuleBasedIntentRouter()
    )
    return build_conversation_service(
        harness=AgentHarness(
            trace_logger=trace_logger,
            hooks=create_default_hooks(),
            intent_router=intent_router,
        ),
        intent_router=intent_router,
        lightweight_llm_client=local_llm_client,
        command_lease_seconds=int(
            os.getenv("SOCIALEASE_CONVERSATION_COMMAND_LEASE_SECONDS", "90")
        ),
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
