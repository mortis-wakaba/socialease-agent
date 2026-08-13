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
    module_memory_request_context,
    module_proposal_copy,
    module_proposal_reason,
    module_skill_name,
    module_start_message,
    module_type_for_intent,
)
from app.conversation.module_coordinator import ModuleCoordinator
from app.conversation.module_policy import ConversationStateError, ModuleStackPolicy
from app.conversation.repository import ConversationRepository, ModuleStartJob
from app.db.factory import repository_factory
from app.llm.base import BaseLLMClient
from app.llm.factory import create_local_llm_client
from app.memory.token_estimator import ConservativeTokenEstimator
from app.memory.active_memory_assembler import ActiveMemoryAssembler
from app.memory.completed_event_writer import CompletedPracticeMemoryWriter
from app.memory.context_orchestrator import MemoryContextOrchestrator
from app.models import ChatRequest, IntentResult, RiskLevel, SafetyResult
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
            )

        intent_result = await self._intent_router.route(message, safety_result)
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
            return await self._response(
                conversation_id=conversation_id,
                user_id=user_id,
                appended_events=[user_event, module_event],
                response=result.response,
                safety_result=safety_result,
                context_diagnostics=context.diagnostics,
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
        return await self._response(
            conversation_id=conversation_id,
            user_id=user_id,
            appended_events=[user_event, replay],
            response=replay.content,
            safety_result=safety_result,
            context_diagnostics=context.diagnostics,
            workflow_response=workflow_response,
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
            return await self._module_coordinator.accept(proposal, context)
        proposal = await self._validated_pending_proposal(
            conversation_id=conversation_id,
            proposal_id=proposal_id,
            user_id=user_id,
            request_hash=request_hash,
        )
        try:
            return await self._module_coordinator.accept(proposal, context)
        except ConversationStateError as exc:
            raise ConversationProposalError(str(exc)) from exc

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
        context = await self._context_manager.assemble(
            conversation_id=conversation_id,
            user_id=user_id,
            current_user_message="结束当前模块",
        )
        return await self._module_coordinator.terminate_current(
            conversation_id=conversation_id,
            user_id=user_id,
            module_run_id=module_run_id,
            context=context,
        )

    async def terminate_all_modules(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ModuleControlResponse:
        """Explicitly terminate all active and suspended frames."""
        return await self._module_coordinator.terminate_all(
            conversation_id=conversation_id,
            user_id=user_id,
        )

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
