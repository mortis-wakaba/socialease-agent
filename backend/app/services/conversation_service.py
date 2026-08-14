"""Application service for unified conversation messages and proposals."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from app.conversation.compactor import ConversationCompactor
from app.conversation.context_manager import ConversationContextManager
from app.conversation.context_provider import create_conversation_context_provider
from app.conversation.adapters import (
    ExposureModuleAdapter,
    ResourceModuleAdapter,
    RoleplayModuleAdapter,
    WorksheetModuleAdapter,
)
from app.conversation.module_catalog import (
    build_module_parameters,
    intent_for_module_type,
    module_memory_request_context,
    module_proposal_copy,
    module_proposal_reason,
    module_skill_name,
    module_start_message,
    module_type_for_intent,
)
from app.conversation.module_coordinator import ModuleCoordinator
from app.conversation.module_policy import ConversationStateError, ModuleStackPolicy
from app.conversation.intent_queue import (
    IntentQueueControl,
    IntentQueueControlAction,
    activate_current_consented_step,
    activate_current_ready_step,
    apply_intent_queue_control,
    complete_current_intent_step,
    detect_intent_queue_control,
    undo_calendar_step_effect,
)
from app.conversation.repository import ConversationRepository, ModuleStartJob
from app.conversation.reversible_actions import (
    ReversibleActionPending,
    ReversibleCalendarExecutor,
)
from app.db.factory import repository_factory
from app.llm.base import BaseLLMClient
from app.llm.factory import create_local_llm_client
from app.memory.token_estimator import ConservativeTokenEstimator
from app.memory.active_memory_assembler import ActiveMemoryAssembler
from app.memory.completed_event_writer import CompletedPracticeMemoryWriter
from app.memory.context_orchestrator import MemoryContextOrchestrator
from app.models import (
    ChatRequest,
    Intent,
    IntentStepEffect,
    IntentExecutionStep,
    IntentResult,
    IntentStepStatus,
    RiskLevel,
    SafetyResult,
)
from app.models_intent_plan import ConversationIntentPlan
from app.models_intent_effect import (
    IntentEffectLedgerRecord,
    IntentEffectLedgerStatus,
)
from app.models_conversation import (
    HISTORY_NOTICE_VERSION,
    Conversation,
    ConversationEventPage,
    ConversationPage,
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ConversationStatus,
    CrisisEscalatedEventPayload,
    ModuleParameters,
    ModuleProposal,
    ModuleProposalEventPayload,
    ModuleProposalStatus,
    ModuleType,
    WorksheetMessageEventPayload,
)
from app.models_conversation_api import ConversationMessageResponse
from app.models_conversation_api import ModuleControlResponse
from app.models_conversation_api import (
    ConversationDeleteResponse,
    ConversationExportCollectionResponse,
    ConversationExportResponse,
)
from app.models_conversation_context import (
    ConversationWorkingContext,
    build_conversation_prompt_context,
)
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response
from app.skills.calendar import build_calendar_event_proposal
from app.workflow.engine import AgentHarness
from app.workflow.context import ResponseChunkSink
from app.workflow.router import BaseIntentRouter, LlmIntentRouter, RuleBasedIntentRouter


class ConversationNoticeError(ValueError):
    """Raised when history persistence has not been explicitly acknowledged."""


class ConversationProposalError(ValueError):
    """Raised when a proposal decision fails validation."""


class ConversationCommandInProgressError(RuntimeError):
    """Raised while another worker owns the same logical message command."""


class ConversationService:
    """Coordinate safety, routing, proposals, and ordered timeline writes."""

    def __init__(
        self,
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
    ) -> None:
        self._harness = harness
        factory = repository_factory()
        self._repository = repository or factory.conversation_repository()
        self._safety_classifier = (
            safety_classifier or create_safety_classifier()
        )
        local_llm_client = lightweight_llm_client or create_local_llm_client()
        if intent_router is None:
            intent_router = (
                LlmIntentRouter(llm_client=local_llm_client)
                if local_llm_client is not None
                else RuleBasedIntentRouter()
            )
        self._intent_router = intent_router
        self._reversible_calendar_executor = (
            reversible_calendar_executor or ReversibleCalendarExecutor()
        )
        estimator = ConservativeTokenEstimator()
        memory_assembler = ActiveMemoryAssembler(token_estimator=estimator)
        self._memory_context_orchestrator = (
            memory_context_orchestrator
            or MemoryContextOrchestrator(
                user_profile_repository=factory.user_profile_repository(),
                settings_repository=factory.user_memory_settings_repository(),
                assembler=memory_assembler,
            )
        )
        self._context_manager = context_manager or ConversationContextManager(
            repository=self._repository,
            compactor=ConversationCompactor(
                llm_client=local_llm_client,
                token_estimator=estimator,
            ),
            token_estimator=estimator,
            provider=create_conversation_context_provider(self._repository),
        )
        completed_memory_writer = CompletedPracticeMemoryWriter(
            memory_repository=factory.long_term_memory_repository(),
            settings_repository=factory.user_memory_settings_repository(),
        )
        self._module_coordinator = module_coordinator or ModuleCoordinator(
            repository=self._repository,
            adapters={
                ModuleType.ROLEPLAY: RoleplayModuleAdapter(
                    memory_writer=completed_memory_writer
                ),
                ModuleType.WORKSHEET: WorksheetModuleAdapter(
                    memory_writer=completed_memory_writer
                ),
                ModuleType.EXPOSURE: ExposureModuleAdapter(
                    memory_writer=completed_memory_writer
                ),
                ModuleType.RESOURCE: ResourceModuleAdapter(),
            },
        )
        self._proposal_ttl = proposal_ttl

    async def create_conversation(
        self,
        *,
        user_id: str,
        title: str,
        history_notice_version: str,
        history_notice_acknowledged: bool,
    ) -> Conversation:
        """Create a durable conversation only after the current notice."""
        if (
            not history_notice_acknowledged
            or history_notice_version != HISTORY_NOTICE_VERSION
        ):
            raise ConversationNoticeError(
                "The current conversation history notice must be acknowledged."
            )
        return await self._repository.create(
            user_id=user_id,
            title=title,
            history_notice_version=history_notice_version,
        )

    async def get_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> Conversation | None:
        """Return one owned conversation."""
        return await self._repository.get_for_user(conversation_id, user_id)

    async def list_conversations(
        self,
        *,
        user_id: str,
        cursor: str | None,
        limit: int,
    ) -> ConversationPage:
        """Return a cursor-paginated owner history list."""
        return await self._repository.list_for_user(
            user_id,
            cursor=cursor,
            limit=limit,
        )


    async def list_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        cursor: str | None,
        limit: int,
    ) -> ConversationEventPage:
        """Return a cursor-paginated owner timeline."""
        return await self._repository.list_events(
            conversation_id=conversation_id,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
        )

    async def list_module_stack(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ):
        """Return active module frames for presentation."""
        return await self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def list_pending_proposals(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleProposal]:
        """Return pending proposals for timeline restoration."""
        now = datetime.now(UTC)
        return [
            proposal
            for proposal in await self._repository.list_proposals(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if proposal.status == ModuleProposalStatus.PENDING
            and proposal.expires_at > now
        ]

    async def get_intent_plan(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationIntentPlan | None:
        """Return the current durable intent plan to its owner."""
        return await self._repository.get_intent_plan(
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def update_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
        expected_version: int,
        title: str | None,
        status,
    ) -> Conversation | None:
        """Rename or archive/unarchive one conversation optimistically."""
        current = await self._repository.get_for_user(conversation_id, user_id)
        if current is None:
            return None
        if title is None and status is None:
            raise ValueError("title or status update is required")
        if status == ConversationStatus.DELETED:
            raise ValueError("use the confirmed delete endpoint")
        if (
            status == ConversationStatus.ARCHIVED
            and await self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        ):
            raise ValueError("end active modules before archiving")
        if status is not None:
            ModuleStackPolicy.validate_conversation_transition(
                current.status,
                status,
            )
        return await self._repository.update_metadata(
            conversation_id=conversation_id,
            user_id=user_id,
            expected_version=expected_version,
            title=title,
            status=status,
        )

    async def export_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationExportResponse | None:
        """Export a complete decrypted timeline only to its owner."""
        conversation = await self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            return None
        events: list[ConversationEvent] = []
        cursor = None
        while True:
            page = await self._repository.list_events(
                conversation_id=conversation_id,
                user_id=user_id,
                cursor=cursor,
                limit=200,
            )
            events.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return ConversationExportResponse(
            conversation=conversation,
            events=events,
            module_runs=await self._repository.list_all_module_runs(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            module_proposals=await self._repository.list_proposals(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            intent_plan=await self._repository.get_intent_plan(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            exported_at=datetime.now(UTC),
        )

    async def export_all_conversations(
        self,
        *,
        user_id: str,
    ) -> ConversationExportCollectionResponse:
        """Export every owner conversation without mixing user scopes."""
        exports: list[ConversationExportResponse] = []
        cursor = None
        while True:
            page = await self._repository.list_for_user(
                user_id,
                cursor=cursor,
                limit=100,
            )
            for conversation in page.items:
                exported = await self.export_conversation(
                    conversation_id=conversation.conversation_id,
                    user_id=user_id,
                )
                if exported is not None:
                    exports.append(exported)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return ConversationExportCollectionResponse(
            user_id=user_id,
            conversations=exports,
            exported_at=datetime.now(UTC),
        )

    async def delete_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationDeleteResponse:
        """Delete durable and runtime data attributable to one conversation."""
        runs = await self._repository.list_all_module_runs(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        await self._module_coordinator.delete_runtime_contexts(runs)
        await self._context_manager.invalidate(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        counts = await self._repository.delete_for_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if counts is None:
            raise LookupError("conversation not found")
        return ConversationDeleteResponse(
            conversation_id=conversation_id,
            deleted=True,
            deleted_counts=counts,
        )

    async def delete_all_conversations(
        self,
        *,
        user_id: str,
    ) -> ConversationDeleteResponse:
        """Delete every owner conversation and its short-lived contexts."""
        page = await self._repository.list_for_user(user_id, limit=100)
        while True:
            for conversation in page.items:
                runs = await self._repository.list_all_module_runs(
                    conversation_id=conversation.conversation_id,
                    user_id=user_id,
                )
                await self._module_coordinator.delete_runtime_contexts(runs)
            if page.next_cursor is None:
                break
            page = await self._repository.list_for_user(
                user_id,
                cursor=page.next_cursor,
                limit=100,
            )
        counts = await self._repository.delete_all_for_user(user_id=user_id)
        await self._context_manager.delete_user_cache(user_id=user_id)
        await self._module_coordinator.delete_user_cache(user_id=user_id)
        return ConversationDeleteResponse(
            conversation_id="all",
            deleted=True,
            deleted_counts=counts,
        )

    async def close(self) -> None:
        """Close the optional shared conversation-context cache client."""
        await self._context_manager.close()
        await self._module_coordinator.close()

    async def delete_user_runtime_contexts(self, *, user_id: str) -> None:
        """Erase rebuildable conversation and module projections for one owner."""
        await self._context_manager.delete_user_cache(user_id=user_id)
        await self._module_coordinator.delete_user_cache(user_id=user_id)

    async def context_health(self) -> bool:
        """Return whether the unified context provider is ready."""
        context_ready = await self._context_manager.health()
        overlay_ready = await self._module_coordinator.health()
        return context_ready and overlay_ready

    async def send_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        idempotency_key: str,
        streaming_requested: bool = False,
        response_chunk_sink: ResponseChunkSink | None = None,
    ) -> ConversationMessageResponse:
        """Execute one logical turn once and durably replay its final result."""
        request_hash = sha256(
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "message": message,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        claim = await self._repository.claim_command(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if claim.completed_result is not None:
            return ConversationMessageResponse.model_validate_json(
                claim.completed_result
            )
        if not claim.acquired:
            raise ConversationCommandInProgressError(
                "conversation command is already processing"
            )
        response = await self._execute_message(
            conversation_id=conversation_id,
            user_id=user_id,
            message=message,
            idempotency_key=idempotency_key,
            streaming_requested=streaming_requested,
            response_chunk_sink=response_chunk_sink,
        )
        await self._repository.complete_command(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            result=response.model_dump_json(),
        )
        return response

    async def _execute_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        idempotency_key: str,
        streaming_requested: bool,
        response_chunk_sink: ResponseChunkSink | None,
    ) -> ConversationMessageResponse:
        """Append the claimed user turn, with crisis and proposal preemption."""
        conversation = await self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise LookupError("conversation not found")

        stack = await self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        active_run = stack[-1] if stack else None
        safety_result = await self._safety_classifier.classify(message)
        user_event = await self._repository.append_event(
            conversation_id=conversation_id,
            user_id=user_id,
            event_type=(
                ConversationEventType.CRISIS_INPUT
                if safety_result.risk_level == RiskLevel.CRISIS
                else (
                    ConversationEventType.MODULE_MESSAGE
                    if active_run
                    else ConversationEventType.USER_MESSAGE
                )
            ),
            role=ConversationEventRole.USER,
            content=message,
            module_run_id=(
                active_run.module_run_id if active_run else None
            ),
            parent_module_run_id=(
                active_run.parent_module_run_id if active_run else None
            ),
            idempotency_key=f"user:{idempotency_key}",
        )
        intent_plan = await self._repository.get_intent_plan(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if safety_result.risk_level == RiskLevel.CRISIS:
            context = await self._context_manager.assemble(
                conversation_id=conversation_id,
                user_id=user_id,
                current_user_message=message,
                current_event_id=user_event.event_id,
            )
            preemption_events = await self._module_coordinator.preempt_for_crisis(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if intent_plan is not None:
                cancelled_plan, _ = apply_intent_queue_control(
                    intent_plan,
                    IntentQueueControl(
                        action=IntentQueueControlAction.CANCEL_ALL
                    ),
                )
                if cancelled_plan is not intent_plan:
                    intent_plan = await self._repository.update_intent_plan(
                        cancelled_plan,
                        expected_version=intent_plan.version,
                    )
            response = crisis_escalation_response(
                paused_activity="当前对话和练习"
            )
            crisis_event = await self._repository.get_event_by_idempotency(
                conversation_id=conversation_id,
                user_id=user_id,
                idempotency_key=f"crisis:{idempotency_key}",
            )
            if crisis_event is None:
                crisis_event = await self._repository.append_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type=ConversationEventType.CRISIS_ESCALATED,
                    role=ConversationEventRole.SYSTEM,
                    content=response,
                    structured_payload=CrisisEscalatedEventPayload(),
                    module_run_id=(
                        stack[-1].module_run_id if stack else None
                    ),
                    parent_module_run_id=(
                        stack[-1].parent_module_run_id if stack else None
                    ),
                    idempotency_key=f"crisis:{idempotency_key}",
                )
            return await self._response(
                conversation_id=conversation_id,
                user_id=user_id,
                appended_events=[user_event, *preemption_events, crisis_event],
                response=response,
                safety_result=safety_result,
                context_diagnostics=context.diagnostics,
                intent_plan=intent_plan,
            )

        queue_control = detect_intent_queue_control(message)
        live_calendar_step = bool(
            intent_plan is not None
            and any(
                step.intent == Intent.CALENDAR_PLANNING
                and step.status
                in {
                    IntentStepStatus.READY,
                    IntentStepStatus.READY_WITH_UNDO,
                    IntentStepStatus.AWAITING_CONSENT,
                    IntentStepStatus.ACTIVE,
                }
                for step in intent_plan.execution_plan.steps
            )
        )
        if (
            queue_control is not None
            and queue_control.action == IntentQueueControlAction.CANCEL_INTENT
            and queue_control.target_intent == Intent.CALENDAR_PLANNING
            and not live_calendar_step
        ):
            ledger_effect = await self._repository.latest_applied_intent_effect(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if (
                ledger_effect is not None
                and ledger_effect.external_action_id is not None
            ):
                undo_key = f"intent-calendar-undo:{ledger_effect.effect_id}"
                try:
                    undo_execution = await self._reversible_calendar_executor.undo(
                        user_id=user_id,
                        calendar_action_id=ledger_effect.external_action_id,
                        idempotency_key=undo_key,
                    )
                except ReversibleActionPending as pending:
                    now = datetime.now(UTC)
                    ledger_effect = await self._repository.update_intent_effect(
                        ledger_effect.model_copy(
                            update={
                                "status": IntentEffectLedgerStatus.UNDO_PENDING,
                                "undo_job_id": pending.job_id,
                                "undo_idempotency_key": undo_key,
                                "updated_at": now,
                            }
                        ),
                        expected_version=ledger_effect.version,
                    )
                    acknowledgement = (
                        "撤销操作已经进入重试队列；重复说“取消提醒”"
                        "会复用同一个撤销任务。"
                    )
                    context = await self._context_manager.assemble(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        current_user_message=message,
                        current_event_id=user_event.event_id,
                    )
                    acknowledgement_event = (
                        await self._repository.append_event(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            event_type=(
                                ConversationEventType.ASSISTANT_MESSAGE
                            ),
                            role=ConversationEventRole.ASSISTANT,
                            content=acknowledgement,
                            idempotency_key=(
                                f"intent-plan-undo-pending:{idempotency_key}"
                            ),
                        )
                    )
                    return await self._response(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        appended_events=[
                            user_event,
                            acknowledgement_event,
                        ],
                        response=acknowledgement,
                        safety_result=safety_result,
                        context_diagnostics=context.diagnostics,
                        intent_plan=intent_plan,
                    )
                now = datetime.now(UTC)
                ledger_effect = await self._repository.update_intent_effect(
                    ledger_effect.model_copy(
                        update={
                            "status": IntentEffectLedgerStatus.UNDONE,
                            "undo_job_id": undo_execution.job_id,
                            "undo_idempotency_key": undo_key,
                            "updated_at": now,
                        }
                    ),
                    expected_version=ledger_effect.version,
                )
                if intent_plan is not None and intent_plan.plan_id == ledger_effect.plan_id:
                    undone = undo_calendar_step_effect(
                        intent_plan,
                        step_id=ledger_effect.step_id,
                    )
                    if undone is not intent_plan:
                        intent_plan = await self._repository.update_intent_plan(
                            undone,
                            expected_version=intent_plan.version,
                        )
                acknowledgement = "好的，之前创建的日历提醒已经撤销。"
                context = await self._context_manager.assemble(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    current_user_message=message,
                    current_event_id=user_event.event_id,
                )
                acknowledgement_event = await self._repository.append_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type=ConversationEventType.ASSISTANT_MESSAGE,
                    role=ConversationEventRole.ASSISTANT,
                    content=acknowledgement,
                    idempotency_key=(
                        f"intent-plan-undo:{idempotency_key}"
                    ),
                )
                return await self._response(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    appended_events=[user_event, acknowledgement_event],
                    response=acknowledgement,
                    safety_result=safety_result,
                    context_diagnostics=context.diagnostics,
                    intent_plan=intent_plan,
                )
        if intent_plan is not None and queue_control is not None:
            previous_plan = intent_plan
            intent_plan, acknowledgement = apply_intent_queue_control(
                previous_plan,
                queue_control,
            )
            if intent_plan is previous_plan:
                intent_plan = previous_plan
            else:
                intent_plan = await self._repository.update_intent_plan(
                    intent_plan,
                    expected_version=previous_plan.version,
                )
            lifecycle_events: list[ConversationEvent] = []
            should_end_active = queue_control.action in {
                IntentQueueControlAction.CANCEL_CURRENT,
                IntentQueueControlAction.CANCEL_ALL,
            }
            if (
                queue_control.action == IntentQueueControlAction.CANCEL_INTENT
                and queue_control.target_intent is not None
                and active_run is not None
            ):
                should_end_active = (
                    module_type_for_intent(queue_control.target_intent)
                    == active_run.module_type
                )
            if stack and should_end_active:
                control_response = (
                    await self._module_coordinator.terminate_all(
                        conversation_id=conversation_id,
                        user_id=user_id,
                    )
                    if queue_control.action
                    == IntentQueueControlAction.CANCEL_ALL
                    else await self._module_coordinator.terminate_current(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        module_run_id=active_run.module_run_id,
                    )
                )
                lifecycle_events.extend(control_response.appended_events)
            context = await self._context_manager.assemble(
                conversation_id=conversation_id,
                user_id=user_id,
                current_user_message=message,
                current_event_id=user_event.event_id,
            )
            progression_events: list[ConversationEvent] = []
            progression_responses: list[str] = []
            source_event = await self._repository.get_event_for_user(
                event_id=intent_plan.source_event_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if source_event is not None:
                original_safety = await self._safety_classifier.classify(
                    source_event.content
                )
                (
                    intent_plan,
                    progression_events,
                    progression_responses,
                ) = await self._consume_ready_intent_steps(
                    plan=intent_plan,
                    source_event=source_event,
                    safety_result=original_safety,
                    context=context,
                )
            combined_acknowledgement = acknowledgement
            if progression_responses:
                combined_acknowledgement += "\n\n接下来：" + "\n\n".join(
                    progression_responses
                )
            acknowledgement_event = await self._repository.append_event(
                conversation_id=conversation_id,
                user_id=user_id,
                event_type=ConversationEventType.ASSISTANT_MESSAGE,
                role=ConversationEventRole.ASSISTANT,
                content=acknowledgement,
                idempotency_key=f"intent-plan-control:{idempotency_key}",
            )
            return await self._response(
                conversation_id=conversation_id,
                user_id=user_id,
                appended_events=[
                    user_event,
                    *lifecycle_events,
                    *progression_events,
                    acknowledgement_event,
                ],
                response=combined_acknowledgement,
                safety_result=safety_result,
                context_diagnostics=context.diagnostics,
                intent_plan=intent_plan,
            )

        if intent_plan is not None and queue_control is None:
            retry_step = _current_intent_step(intent_plan)
            if (
                retry_step is not None
                and retry_step.status == IntentStepStatus.READY_WITH_UNDO
                and retry_step.intent == Intent.CALENDAR_PLANNING
            ):
                source_event = await self._repository.get_event_for_user(
                    event_id=intent_plan.source_event_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                if (
                    source_event is not None
                    and source_event.content.strip() == message.strip()
                ):
                    context = await self._context_manager.assemble(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        current_user_message=message,
                        current_event_id=user_event.event_id,
                    )
                    (
                        intent_plan,
                        retry_events,
                        retry_responses,
                    ) = await self._consume_ready_intent_steps(
                        plan=intent_plan,
                        source_event=source_event,
                        safety_result=safety_result,
                        context=context,
                    )
                    if retry_responses:
                        return await self._response(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            appended_events=[user_event, *retry_events],
                            response="\n\n".join(retry_responses),
                            safety_result=safety_result,
                            context_diagnostics=context.diagnostics,
                            intent_plan=intent_plan,
                        )

        intent_result = await self._intent_router.route(message, safety_result)
        if (
            not stack
            and intent_result.execution_plan is not None
            and intent_result.execution_plan.steps
            and (
                len(intent_result.execution_plan.steps) > 1
                or intent_result.execution_plan.steps[0].status
                == IntentStepStatus.READY_WITH_UNDO
            )
        ):
            now = datetime.now(UTC)
            intent_plan = await self._repository.save_intent_plan(
                ConversationIntentPlan(
                    plan_id=uuid4().hex,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    source_event_id=user_event.event_id,
                    execution_plan=intent_result.execution_plan,
                    created_at=now,
                    updated_at=now,
                )
            )
        primary_step = (
            intent_result.execution_plan.steps[0]
            if intent_result.execution_plan is not None
            and intent_result.execution_plan.steps
            else None
        )
        if (
            intent_plan is not None
            and primary_step is not None
            and primary_step.status == IntentStepStatus.READY_WITH_UNDO
        ):
            context = await self._context_manager.assemble(
                conversation_id=conversation_id,
                user_id=user_id,
                current_user_message=message,
                current_event_id=user_event.event_id,
            )
            (
                intent_plan,
                effect_events,
                effect_responses,
            ) = await self._consume_ready_intent_steps(
                plan=intent_plan,
                source_event=user_event,
                safety_result=safety_result,
                context=context,
            )
            if effect_responses:
                return await self._response(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    appended_events=[user_event, *effect_events],
                    response="\n\n".join(effect_responses),
                    safety_result=safety_result,
                    context_diagnostics=context.diagnostics,
                    intent_plan=intent_plan,
                )
        proposed_module = module_type_for_intent(intent_result.intent)
        if (
            proposed_module is not None
            and safety_result.risk_level == RiskLevel.LOW
            and ModuleStackPolicy.can_push(stack, proposed_module)
        ):
            context = await self._context_manager.assemble(
                conversation_id=conversation_id,
                user_id=user_id,
                current_user_message=message,
                current_event_id=user_event.event_id,
            )
            proposal = await self._create_proposal(
                conversation_id=conversation_id,
                user_id=user_id,
                source_event=user_event,
                message=message,
                module_type=proposed_module,
            )
            if (
                primary_step is not None
                and primary_step.status == IntentStepStatus.READY
            ):
                started = await self._module_coordinator.accept(
                    proposal,
                    context,
                )
                if intent_plan is not None:
                    prior_version = intent_plan.version
                    intent_plan.execution_plan.steps[0].status = (
                        IntentStepStatus.ACTIVE
                    )
                    intent_plan.updated_at = datetime.now(UTC)
                    intent_plan = await self._repository.update_intent_plan(
                        intent_plan,
                        expected_version=prior_version,
                    )
                return await self._response(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    appended_events=[user_event, *started.appended_events],
                    response=started.response,
                    safety_result=safety_result,
                    context_diagnostics=context.diagnostics,
                    intent_plan=intent_plan,
                )
            proposal_copy = module_proposal_copy(proposed_module)
            proposal_event = await self._repository.get_event_by_idempotency(
                conversation_id=conversation_id,
                user_id=user_id,
                idempotency_key=f"proposal:{idempotency_key}",
            )
            if proposal_event is None:
                proposal_event = await self._repository.append_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type=ConversationEventType.MODULE_PROPOSED,
                    role=ConversationEventRole.ASSISTANT,
                    content=proposal_copy,
                    structured_payload=ModuleProposalEventPayload(
                        proposal_id=proposal.proposal_id,
                        proposed_module=proposal.proposed_module,
                        reason_code=proposal.reason_code,
                    ),
                    module_run_id=(
                        stack[-1].module_run_id if stack else None
                    ),
                    parent_module_run_id=(
                        stack[-1].parent_module_run_id if stack else None
                    ),
                    idempotency_key=f"proposal:{idempotency_key}",
                )
            return await self._response(
                conversation_id=conversation_id,
                user_id=user_id,
                appended_events=[user_event, proposal_event],
                response=proposal_copy,
                safety_result=safety_result,
                context_diagnostics=context.diagnostics,
                pending_proposal=proposal,
                intent_plan=intent_plan,
            )

        if stack:
            context = await self._assemble_module_context(
                conversation_id=conversation_id,
                user_id=user_id,
                message=message,
                module_type=active_run.module_type,
                module_parameters=active_run.module_parameters,
                current_event_id=user_event.event_id,
            )
            (
                result,
                module_event,
                context,
            ) = await self._module_coordinator.handle_message(
                conversation_id=conversation_id,
                user_id=user_id,
                message=message,
                idempotency_key=idempotency_key,
                context=context,
            )
            completion_events: list[ConversationEvent] = []
            progression_responses: list[str] = []
            if (
                intent_plan is not None
                and isinstance(
                    result.event_payload,
                    WorksheetMessageEventPayload,
                )
                and result.event_payload.completed
            ):
                ended = await self._module_coordinator.terminate_current(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    module_run_id=active_run.module_run_id,
                    context=context,
                )
                completion_events.extend(ended.appended_events)
                source_event = await self._repository.get_event_for_user(
                    event_id=intent_plan.source_event_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                if source_event is not None:
                    original_safety = await self._safety_classifier.classify(
                        source_event.content
                    )
                    (
                        intent_plan,
                        progression_events,
                        progression_responses,
                    ) = await self._complete_and_consume_intent_plan(
                        plan=intent_plan,
                        completed_intent=Intent.CBT_WORKSHEET,
                        source_event=source_event,
                        safety_result=original_safety,
                        context=context,
                    )
                    completion_events.extend(progression_events)
            combined_response = result.response
            if progression_responses:
                combined_response += "\n\n接下来：" + "\n\n".join(
                    progression_responses
                )
            return await self._response(
                conversation_id=conversation_id,
                user_id=user_id,
                appended_events=[user_event, module_event, *completion_events],
                response=combined_response,
                safety_result=safety_result,
                context_diagnostics=context.diagnostics,
                intent_plan=intent_plan,
            )

        assistant_key = f"assistant:{idempotency_key}"
        context = await self._context_manager.assemble(
            conversation_id=conversation_id,
            user_id=user_id,
            current_user_message=message,
            current_event_id=user_event.event_id,
        )
        replay = await self._repository.get_event_by_idempotency(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=assistant_key,
        )
        workflow_response = None
        if replay is None:
            harness_kwargs = {
                "trusted_safety_result": safety_result,
                "trusted_intent_result": intent_result,
                "trusted_conversation_context": build_conversation_prompt_context(
                    context
                ),
                "streaming_requested": streaming_requested,
            }
            if response_chunk_sink is not None:
                harness_kwargs["response_chunk_sink"] = response_chunk_sink
            workflow_response = await self._harness.run(
                ChatRequest(
                    user_id=user_id,
                    message=message,
                    context=_workflow_context(
                        context,
                        source_event_id=user_event.event_id,
                    ),
                ),
                **harness_kwargs,
            )
            replay = await self._repository.append_event(
                conversation_id=conversation_id,
                user_id=user_id,
                event_type=ConversationEventType.ASSISTANT_MESSAGE,
                role=ConversationEventRole.ASSISTANT,
                content=workflow_response.response,
                idempotency_key=assistant_key,
            )
        progression_events: list[ConversationEvent] = []
        progression_responses: list[str] = []
        if intent_plan is not None:
            (
                intent_plan,
                progression_events,
                progression_responses,
            ) = await self._complete_and_consume_intent_plan(
                plan=intent_plan,
                completed_intent=intent_result.intent,
                source_event=user_event,
                safety_result=safety_result,
                context=context,
            )
        combined_response = replay.content
        if progression_responses:
            combined_response += "\n\n接下来：" + "\n\n".join(
                progression_responses
            )
        return await self._response(
            conversation_id=conversation_id,
            user_id=user_id,
            appended_events=[user_event, replay, *progression_events],
            response=combined_response,
            safety_result=safety_result,
            context_diagnostics=context.diagnostics,
            workflow_response=workflow_response,
            intent_plan=intent_plan,
        )

    async def reject_proposal(
        self,
        *,
        conversation_id: str,
        proposal_id: str,
        user_id: str,
        request_hash: str,
    ) -> ModuleProposal:
        """Reject a pending, unexpired, untampered proposal."""
        proposal = await self._validated_pending_proposal(
            conversation_id=conversation_id,
            proposal_id=proposal_id,
            user_id=user_id,
            request_hash=request_hash,
        )
        rejected = await self._repository.transition_proposal(
            proposal_id=proposal_id,
            conversation_id=conversation_id,
            user_id=user_id,
            expected_status=ModuleProposalStatus.PENDING,
            target_status=ModuleProposalStatus.REJECTED,
        )
        if rejected is None:
            raise ConversationProposalError("proposal not found")
        return rejected

    async def accept_proposal(
        self,
        *,
        conversation_id: str,
        proposal_id: str,
        user_id: str,
        request_hash: str,
    ) -> ModuleControlResponse:
        """Accept one proposal and delegate the confirmed stack push."""
        proposal = await self._repository.get_proposal_for_user(
            proposal_id=proposal_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if proposal is None:
            raise ConversationProposalError("proposal not found")
        if proposal.request_hash != request_hash:
            raise ConversationProposalError("proposal request hash mismatch")
        context = await self._assemble_module_context(
            conversation_id=conversation_id,
            user_id=user_id,
            message=module_start_message(proposal),
            module_type=proposal.proposed_module,
            module_parameters=proposal.bounded_parameters,
            current_event_id=proposal.source_event_id,
        )
        if proposal.status == ModuleProposalStatus.ACCEPTED:
            result = await self._module_coordinator.accept(proposal, context)
        else:
            proposal = await self._validated_pending_proposal(
                conversation_id=conversation_id,
                proposal_id=proposal_id,
                user_id=user_id,
                request_hash=request_hash,
            )
            try:
                result = await self._module_coordinator.accept(
                    proposal,
                    context,
                )
            except ConversationStateError as exc:
                raise ConversationProposalError(str(exc)) from exc
        plan = await self._repository.get_intent_plan(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if plan is not None:
            activated = activate_current_consented_step(
                plan,
                expected_intent=intent_for_module_type(
                    proposal.proposed_module
                ),
            )
            if activated is not plan:
                plan = await self._repository.update_intent_plan(
                    activated,
                    expected_version=plan.version,
                )
        return result.model_copy(update={"intent_plan": plan})

    async def reconcile_module_start(
        self,
        job: ModuleStartJob,
    ) -> ModuleControlResponse:
        """Reconcile one lease-owned module startup without a client retry."""
        run = await self._repository.get_module_run_for_user(
            module_run_id=job.module_run_id,
            conversation_id=job.conversation_id,
            user_id=job.user_id,
        )
        proposal = await self._repository.get_proposal_for_user(
            proposal_id=job.proposal_id,
            conversation_id=job.conversation_id,
            user_id=job.user_id,
        )
        if run is None or proposal is None:
            raise LookupError("module reconciliation state not found")
        context = await self._assemble_module_context(
            conversation_id=job.conversation_id,
            user_id=job.user_id,
            message=module_start_message(proposal),
            module_type=proposal.proposed_module,
            module_parameters=proposal.bounded_parameters,
            current_event_id=proposal.source_event_id,
        )
        return await self._module_coordinator.reconcile_claimed(
            run=run,
            context=context,
            proposal_id=job.proposal_id,
        )

    async def terminate_current_module(
        self,
        *,
        conversation_id: str,
        module_run_id: str,
        user_id: str,
    ) -> ModuleControlResponse:
        """Explicitly terminate the active top module."""
        run = await self._repository.get_module_run_for_user(
            module_run_id=module_run_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if run is None:
            raise LookupError("active module not found")
        context = await self._context_manager.assemble(
            conversation_id=conversation_id,
            user_id=user_id,
            current_user_message="结束当前模块",
        )
        terminated = await self._module_coordinator.terminate_current(
            conversation_id=conversation_id,
            user_id=user_id,
            module_run_id=module_run_id,
            context=context,
        )
        plan = await self._repository.get_intent_plan(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        progression_events: list[ConversationEvent] = []
        progression_responses: list[str] = []
        if plan is not None:
            source_event = await self._repository.get_event_for_user(
                event_id=plan.source_event_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if source_event is not None:
                safety_result = await self._safety_classifier.classify(
                    source_event.content
                )
                (
                    plan,
                    progression_events,
                    progression_responses,
                ) = await self._complete_and_consume_intent_plan(
                    plan=plan,
                    completed_intent=intent_for_module_type(run.module_type),
                    source_event=source_event,
                    safety_result=safety_result,
                    context=context,
                )
        conversation = await self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise LookupError("conversation not found")
        response = terminated.response
        if progression_responses:
            response += "\n\n接下来：" + "\n\n".join(
                progression_responses
            )
        return ModuleControlResponse(
            conversation=conversation,
            active_module_stack=await self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            appended_events=[
                *terminated.appended_events,
                *progression_events,
            ],
            response=response,
            intent_plan=plan,
        )

    async def terminate_all_modules(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ModuleControlResponse:
        """Explicitly terminate all active and suspended frames."""
        result = await self._module_coordinator.terminate_all(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        plan = await self._repository.get_intent_plan(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if plan is not None:
            cancelled, _ = apply_intent_queue_control(
                plan,
                IntentQueueControl(
                    action=IntentQueueControlAction.CANCEL_ALL
                ),
            )
            if cancelled is not plan:
                plan = await self._repository.update_intent_plan(
                    cancelled,
                    expected_version=plan.version,
                )
        return result.model_copy(update={"intent_plan": plan})

    async def _assemble_module_context(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        module_type: ModuleType,
        module_parameters: ModuleParameters | None = None,
        current_event_id: str | None = None,
    ) -> ConversationWorkingContext:
        """Load stable memory and allocate the shared module context."""
        skill_name = module_skill_name(module_type)
        active_memory = None
        if skill_name is not None:
            active_memory = await self._memory_context_orchestrator.assemble(
                user_id=user_id,
                skill_name=skill_name,
                current_request=message,
                request_context=module_memory_request_context(
                    module_type,
                    message,
                    module_parameters,
                ),
            )
        return await self._context_manager.assemble(
            conversation_id=conversation_id,
            user_id=user_id,
            current_user_message=message,
            current_event_id=current_event_id,
            active_memory=active_memory,
        )

    async def _complete_and_consume_intent_plan(
        self,
        *,
        plan: ConversationIntentPlan,
        completed_intent: Intent,
        source_event: ConversationEvent,
        safety_result: SafetyResult,
        context: ConversationWorkingContext,
    ) -> tuple[ConversationIntentPlan, list[ConversationEvent], list[str]]:
        """Persist completion, then consume consecutive safe ready steps."""
        completed = complete_current_intent_step(
            plan,
            expected_intent=completed_intent,
        )
        if completed is plan:
            return plan, [], []
        plan = await self._repository.update_intent_plan(
            completed,
            expected_version=plan.version,
        )
        return await self._consume_ready_intent_steps(
            plan=plan,
            source_event=source_event,
            safety_result=safety_result,
            context=context,
        )

    async def _consume_ready_intent_steps(
        self,
        *,
        plan: ConversationIntentPlan,
        source_event: ConversationEvent,
        safety_result: SafetyResult,
        context: ConversationWorkingContext,
    ) -> tuple[ConversationIntentPlan, list[ConversationEvent], list[str]]:
        """Execute ready steps serially, stopping at modules or permission gates."""
        events: list[ConversationEvent] = []
        responses: list[str] = []
        if safety_result.risk_level != RiskLevel.LOW:
            return plan, events, responses
        for _ in range(2):
            current = _current_intent_step(plan)
            if current is None:
                break
            if current.status == IntentStepStatus.READY_WITH_UNDO:
                if current.intent != Intent.CALENDAR_PLANNING:
                    break
                proposal = build_calendar_event_proposal(source_event.content)
                if proposal is None:
                    break
                idempotency_key = (
                    f"intent-calendar:{plan.plan_id}:{current.step_id}"
                )
                try:
                    calendar_execution = await self._reversible_calendar_executor.create(
                        user_id=plan.user_id,
                        proposal=proposal,
                        idempotency_key=idempotency_key,
                    )
                except ReversibleActionPending as pending:
                    now = datetime.now(UTC)
                    await self._repository.save_intent_effect(
                        IntentEffectLedgerRecord(
                            effect_id=uuid4().hex,
                            plan_id=plan.plan_id,
                            step_id=current.step_id,
                            conversation_id=plan.conversation_id,
                            user_id=plan.user_id,
                            kind="calendar_event",
                            status=IntentEffectLedgerStatus.APPLY_PENDING,
                            create_job_id=pending.job_id,
                            create_idempotency_key=idempotency_key,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    response = (
                        "日历写入已经进入重试队列，系统会复用同一个任务，"
                        "不会重复创建事件。你也可以稍后重复原请求查询结果。"
                    )
                    event = await self._repository.append_event(
                        conversation_id=plan.conversation_id,
                        user_id=plan.user_id,
                        event_type=ConversationEventType.ASSISTANT_MESSAGE,
                        role=ConversationEventRole.ASSISTANT,
                        content=response,
                        idempotency_key=(
                            f"intent-plan-effect-pending:{plan.plan_id}:"
                            f"{current.step_id}"
                        ),
                    )
                    events.append(event)
                    responses.append(response)
                    break
                calendar_result = calendar_execution.response
                now = datetime.now(UTC)
                stored_effect = await self._repository.save_intent_effect(
                    IntentEffectLedgerRecord(
                        effect_id=uuid4().hex,
                        plan_id=plan.plan_id,
                        step_id=current.step_id,
                        conversation_id=plan.conversation_id,
                        user_id=plan.user_id,
                        kind="calendar_event",
                        status=IntentEffectLedgerStatus.APPLIED,
                        create_job_id=calendar_execution.job_id,
                        external_action_id=(
                            calendar_result.event.calendar_action_id
                        ),
                        create_idempotency_key=idempotency_key,
                        created_at=now,
                        updated_at=now,
                    )
                )
                if stored_effect.status == IntentEffectLedgerStatus.APPLY_PENDING:
                    stored_effect = await self._repository.update_intent_effect(
                        stored_effect.model_copy(
                            update={
                                "status": IntentEffectLedgerStatus.APPLIED,
                                "external_action_id": (
                                    calendar_result.event.calendar_action_id
                                ),
                                "updated_at": now,
                            }
                        ),
                        expected_version=stored_effect.version,
                    )
                response = (
                    "已按你的明确请求写入日历："
                    f"{calendar_result.event.proposal.title}，"
                    f"{calendar_result.event.proposal.start_time.isoformat()}。"
                    "如果不需要了，直接说“取消提醒”即可撤销。"
                )
                event = await self._repository.append_event(
                    conversation_id=plan.conversation_id,
                    user_id=plan.user_id,
                    event_type=ConversationEventType.ASSISTANT_MESSAGE,
                    role=ConversationEventRole.ASSISTANT,
                    content=response,
                    idempotency_key=(
                        f"intent-plan-effect:{plan.plan_id}:{current.step_id}"
                    ),
                )
                events.append(event)
                responses.append(response)
                completed = complete_current_intent_step(
                    plan,
                    expected_intent=current.intent,
                    effect=IntentStepEffect(
                        external_action_id=(
                            calendar_result.event.calendar_action_id
                        ),
                        idempotency_key=idempotency_key,
                        applied_at=datetime.now(UTC),
                    ),
                )
                if completed is plan:
                    break
                plan = await self._repository.update_intent_plan(
                    completed,
                    expected_version=plan.version,
                )
                continue
            if current.status != IntentStepStatus.READY:
                break
            stack = await self._repository.list_module_stack(
                conversation_id=plan.conversation_id,
                user_id=plan.user_id,
            )
            if stack:
                break
            module_type = module_type_for_intent(current.intent)
            if module_type is not None:
                proposal = await self._create_proposal(
                    conversation_id=plan.conversation_id,
                    user_id=plan.user_id,
                    source_event=source_event,
                    message=source_event.content,
                    module_type=module_type,
                )
                module_context = await self._assemble_module_context(
                    conversation_id=plan.conversation_id,
                    user_id=plan.user_id,
                    message=source_event.content,
                    module_type=module_type,
                    module_parameters=proposal.bounded_parameters,
                    current_event_id=source_event.event_id,
                )
                started = await self._module_coordinator.accept(
                    proposal,
                    module_context,
                )
                activated = activate_current_ready_step(
                    plan,
                    expected_step_id=current.step_id,
                )
                if activated is not plan:
                    plan = await self._repository.update_intent_plan(
                        activated,
                        expected_version=plan.version,
                    )
                events.extend(started.appended_events)
                responses.append(started.response)
                break

            workflow = await self._harness.run(
                ChatRequest(
                    user_id=plan.user_id,
                    message=source_event.content,
                    context=_workflow_context(
                        context,
                        source_event_id=source_event.event_id,
                    ),
                ),
                trusted_safety_result=safety_result,
                trusted_intent_result=IntentResult(
                    intent=current.intent,
                    confidence=1.0,
                    reason="authorized sequential plan progression",
                ),
                trusted_conversation_context=build_conversation_prompt_context(
                    context
                ),
                streaming_requested=False,
            )
            event = await self._repository.append_event(
                conversation_id=plan.conversation_id,
                user_id=plan.user_id,
                event_type=ConversationEventType.ASSISTANT_MESSAGE,
                role=ConversationEventRole.ASSISTANT,
                content=workflow.response,
                idempotency_key=(
                    f"intent-plan-step:{plan.plan_id}:{current.step_id}"
                ),
            )
            events.append(event)
            responses.append(workflow.response)
            completed = complete_current_intent_step(
                plan,
                expected_intent=current.intent,
            )
            if completed is plan:
                break
            plan = await self._repository.update_intent_plan(
                completed,
                expected_version=plan.version,
            )
        return plan, events, responses

    async def _validated_pending_proposal(
        self,
        *,
        conversation_id: str,
        proposal_id: str,
        user_id: str,
        request_hash: str,
    ) -> ModuleProposal:
        proposal = await self._repository.get_proposal_for_user(
            proposal_id=proposal_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if proposal is None:
            raise ConversationProposalError("proposal not found")
        if proposal.request_hash != request_hash:
            raise ConversationProposalError("proposal request hash mismatch")
        if proposal.status != ModuleProposalStatus.PENDING:
            raise ConversationProposalError("proposal is no longer pending")
        if proposal.expires_at <= datetime.now(UTC):
            await self._repository.transition_proposal(
                proposal_id=proposal_id,
                conversation_id=conversation_id,
                user_id=user_id,
                expected_status=ModuleProposalStatus.PENDING,
                target_status=ModuleProposalStatus.EXPIRED,
            )
            raise ConversationProposalError("proposal expired")
        return proposal

    async def _create_proposal(
        self,
        *,
        conversation_id: str,
        user_id: str,
        source_event: ConversationEvent,
        message: str,
        module_type: ModuleType,
    ) -> ModuleProposal:
        parameters = build_module_parameters(module_type, message)
        request_hash = _proposal_request_hash(
            conversation_id=conversation_id,
            user_id=user_id,
            source_event_id=source_event.event_id,
            module_type=module_type,
            parameters=parameters.model_dump(mode="json"),
        )
        proposal = ModuleProposal(
            proposal_id=uuid4().hex,
            conversation_id=conversation_id,
            user_id=user_id,
            proposed_module=module_type,
            source_event_id=source_event.event_id,
            reason_code=module_proposal_reason(module_type),
            bounded_parameters=parameters,
            request_hash=request_hash,
            expires_at=datetime.now(UTC) + self._proposal_ttl,
            created_at=datetime.now(UTC),
        )
        return await self._repository.save_proposal(proposal)

    async def _response(
        self,
        *,
        conversation_id: str,
        user_id: str,
        appended_events: list[ConversationEvent],
        response: str,
        safety_result: SafetyResult,
        context_diagnostics,
        pending_proposal: ModuleProposal | None = None,
        workflow_response=None,
        intent_plan: ConversationIntentPlan | None = None,
    ) -> ConversationMessageResponse:
        conversation = await self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise LookupError("conversation not found")
        return ConversationMessageResponse(
            conversation=conversation,
            appended_events=appended_events,
            active_module_stack=await self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            pending_module_proposal=pending_proposal,
            intent_plan=(
                intent_plan
                if intent_plan is not None
                else await self._repository.get_intent_plan(
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
            ),
            response=response,
            safety_result=safety_result,
            context_diagnostics=context_diagnostics,
            workflow_response=workflow_response,
        )


def _proposal_request_hash(
    *,
    conversation_id: str,
    user_id: str,
    source_event_id: str,
    module_type: ModuleType,
    parameters: dict[str, object],
) -> str:
    payload = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "source_event_id": source_event_id,
        "module_type": module_type.value,
        "parameters": parameters,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _current_intent_step(
    plan: ConversationIntentPlan,
) -> IntentExecutionStep | None:
    """Return the first nonterminal step from authoritative plan state."""
    live_statuses = {
        IntentStepStatus.READY,
        IntentStepStatus.READY_WITH_UNDO,
        IntentStepStatus.AWAITING_CONSENT,
        IntentStepStatus.ACTIVE,
    }
    return next(
        (
            step
            for step in plan.execution_plan.steps
            if step.status in live_statuses
        ),
        None,
    )


def _workflow_context(
    context,
    *,
    source_event_id: str,
) -> dict[str, object]:
    """Project bounded conversation context into the existing harness."""
    return {
        "session_id": context.conversation_id,
        "request_id": source_event_id,
        "conversation_id": context.conversation_id,
        "module_stack": [
            {
                "module_type": run.module_type.value,
                "status": run.status.value,
                "depth": run.depth,
            }
            for run in context.active_module_stack
        ],
    }
