"""Application service for unified conversation messages and proposals."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
from collections.abc import Callable
from uuid import uuid4

from app.conversation.context_manager import ConversationContextManager
from app.conversation.module_catalog import (
    build_module_parameters,
    intent_for_module_type,
    module_memory_request_context,
    module_proposal_reason,
    module_skill_name,
    module_start_message,
)
from app.conversation.module_coordinator import ModuleCoordinator
from app.conversation.module_policy import ConversationStateError, ModuleStackPolicy
from app.conversation.intent_queue import (
    IntentQueueControl,
    IntentQueueControlAction,
    activate_current_consented_step,
    apply_intent_queue_control,
    complete_current_intent_step,
)
from app.conversation.intent_effect_executor import IntentEffectExecutor
from app.conversation.intent_step_runner import (
    IntentStepRunner,
    conversation_workflow_context,
)
from app.conversation.repository import (
    ConversationCommandRecord,
    ConversationConcurrencyError,
    ConversationRepository,
    ModuleStartJob,
)
from app.conversation.reversible_actions import ReversibleCalendarExecutor
from app.conversation.turns import ConversationTurn
from app.conversation.turn_classification import ConcurrentTurnClassifier
from app.conversation.turn_classification import TurnClassification
from app.conversation.turn_coordinators import (
    IntentDispatchCoordinator,
    IntentQueueCoordinator,
    TurnResolution,
)
from app.memory.context_orchestrator import MemoryContextOrchestrator
from app.models import (
    ChatRequest,
    Intent,
    IntentResult,
    RiskLevel,
    SafetyResult,
)
from app.models_intent_plan import ConversationIntentPlan
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
    ConversationContextDiagnostics,
    ConversationContextProfile,
    ConversationWorkingContext,
    build_conversation_prompt_context,
    conversation_id_hash,
)
from app.safety.classifier import BaseSafetyClassifier
from app.safety.crisis import crisis_escalation_response
from app.observability.turn_latency import (
    ConversationTurnLatencySink,
    mark_first_turn_output,
    measure_turn_stage,
    reset_turn_latency,
    start_turn_latency,
)
from app.workflow.engine import AgentHarness
from app.workflow.context import ResponseChunkSink
from app.workflow.router import BaseIntentRouter


class ConversationNoticeError(ValueError):
    """Raised when history persistence has not been explicitly acknowledged."""


class ConversationProposalError(ValueError):
    """Raised when a proposal decision fails validation."""


class ConversationCommandInProgressError(RuntimeError):
    """Raised while another worker owns the same logical message command."""


class ConversationLeaseLostError(ConversationCommandInProgressError):
    """Raised when a worker must stop because its durable lease was lost."""


logger = logging.getLogger(__name__)


class ConversationService:
    """Coordinate safety, routing, proposals, and ordered timeline writes."""

    def __init__(
        self,
        *,
        harness: AgentHarness,
        repository: ConversationRepository,
        safety_classifier: BaseSafetyClassifier,
        intent_router: BaseIntentRouter,
        context_manager: ConversationContextManager,
        module_coordinator: ModuleCoordinator,
        memory_context_orchestrator: MemoryContextOrchestrator,
        reversible_calendar_executor: ReversibleCalendarExecutor,
        proposal_ttl: timedelta = timedelta(minutes=15),
        command_lease_seconds: int = 90,
        latency_sink: ConversationTurnLatencySink | None = None,
    ) -> None:
        self._harness = harness
        self._repository = repository
        self._safety_classifier = safety_classifier
        self._intent_router = intent_router
        self._reversible_calendar_executor = reversible_calendar_executor
        self._memory_context_orchestrator = memory_context_orchestrator
        self._context_manager = context_manager
        self._module_coordinator = module_coordinator
        self._proposal_ttl = proposal_ttl
        self._command_lease_seconds = max(15, command_lease_seconds)
        self._latency_sink = latency_sink
        self._turn_classifier = ConcurrentTurnClassifier(
            classify_safety=self._classify_safety,
            route_intent=self._route_intent,
        )
        self._intent_queue_coordinator = IntentQueueCoordinator(
            repository=self._repository,
            safety_classifier=self._safety_classifier,
            context_manager=self._context_manager,
            module_coordinator=self._module_coordinator,
            reversible_calendar_executor=self._reversible_calendar_executor,
            consume_ready_steps=self._consume_ready_intent_steps,
        )
        self._intent_effect_executor = IntentEffectExecutor(
            repository=self._repository,
            calendar_executor=self._reversible_calendar_executor,
        )
        self._intent_step_runner = IntentStepRunner(
            repository=self._repository,
            harness=self._harness,
            effect_executor=self._intent_effect_executor,
            module_coordinator=self._module_coordinator,
            create_proposal=self._create_proposal,
            assemble_module_context=self._assemble_module_context,
        )
        self._intent_dispatch_coordinator = IntentDispatchCoordinator(
            repository=self._repository,
            route_intent=self._route_intent,
            context_manager=self._context_manager,
            module_coordinator=self._module_coordinator,
            consume_ready_steps=self._consume_ready_intent_steps,
            create_proposal=self._create_proposal,
        )

    async def _route_intent(
        self,
        message: str,
        safety_result: SafetyResult,
    ) -> IntentResult:
        """Route through the service's current strategy."""
        with measure_turn_stage("routing"):
            return await self._intent_router.route(message, safety_result)

    async def _classify_safety(self, message: str) -> SafetyResult:
        """Classify input risk with request-local stage timing."""
        with measure_turn_stage("safety"):
            return await self._safety_classifier.classify(message)

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

    async def get_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationCommandRecord | None:
        """Return owner-scoped command recovery state without request content."""
        return await self._repository.get_command(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

    async def abandon_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationCommandRecord | None:
        """Terminally abandon an expired command through the repository fence."""
        return await self._repository.abandon_command(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

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
        recorder, latency_token = start_turn_latency(
            streaming_requested=streaming_requested,
        )
        original_chunk_sink = response_chunk_sink

        async def measured_chunk_sink(text: str) -> None:
            mark_first_turn_output()
            if original_chunk_sink is not None:
                await original_chunk_sink(text)

        if response_chunk_sink is not None:
            response_chunk_sink = measured_chunk_sink
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
        lease_owner = uuid4().hex
        crisis_turn: ConversationTurn | None = None
        classification_override: TurnClassification | None = None

        def remember_crisis_turn(turn: ConversationTurn) -> None:
            nonlocal crisis_turn
            crisis_turn = turn

        try:
            with measure_turn_stage("command_claim"):
                claim = await self._repository.claim_command(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    lease_owner=lease_owner,
                    lease_seconds=self._command_lease_seconds,
                )
            if claim.completed_result is not None:
                return ConversationMessageResponse.model_validate_json(
                    claim.completed_result
                )
            if not claim.acquired:
                busy_safety = await self._classify_safety(message)
                if busy_safety.risk_level is not RiskLevel.CRISIS:
                    raise ConversationCommandInProgressError(
                        "conversation command is already processing"
                    )
                classification_override = TurnClassification(
                    safety_result=busy_safety,
                    intent_result=None,
                )
                if claim.conversation is None:
                    raise ConversationConcurrencyError(
                        "owner-validated command claim omitted conversation state"
                    )
                crisis_turn = await self._unclaimed_crisis_turn(
                    conversation=claim.conversation,
                    message=message,
                    idempotency_key=idempotency_key,
                    lease_owner=lease_owner,
                    safety_result=busy_safety,
                    streaming_requested=streaming_requested,
                    response_chunk_sink=response_chunk_sink,
                )
                try:
                    claim = await self._repository.claim_command(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        lease_owner=lease_owner,
                        lease_seconds=self._command_lease_seconds,
                        preempt_active=True,
                    )
                except BaseException as exc:
                    _reraise_external_cancellation(exc)
                    logger.error(
                        "Crisis priority command claim failed: %s",
                        type(exc).__name__,
                    )
                    mark_first_turn_output()
                    return _degraded_crisis_response(crisis_turn)
                if claim.completed_result is not None:
                    return ConversationMessageResponse.model_validate_json(
                        claim.completed_result
                    )
                if not claim.acquired:
                    mark_first_turn_output()
                    return _degraded_crisis_response(crisis_turn)
            safety_recovery_already_completed = (
                claim.safety_recovery_status == "completed"
            )
            if safety_recovery_already_completed:
                classification_override = TurnClassification(
                    safety_result=SafetyResult(
                        risk_level=RiskLevel.CRISIS,
                        reason="durable completed crisis safety recovery",
                    ),
                    intent_result=None,
                )
            execution_task: asyncio.Task[ConversationMessageResponse] | None = None
            lease_task: asyncio.Task[None] | None = None
            try:
                with self._repository.command_lease_scope(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    lease_owner=lease_owner,
                ):
                    async def execute_turn() -> ConversationMessageResponse:
                        with measure_turn_stage("turn_execution"):
                            return await self._execute_message(
                                conversation_id=conversation_id,
                                user_id=user_id,
                                message=message,
                                idempotency_key=idempotency_key,
                                lease_owner=lease_owner,
                                streaming_requested=streaming_requested,
                                response_chunk_sink=response_chunk_sink,
                                classification_override=classification_override,
                                on_crisis_identified=remember_crisis_turn,
                                safety_recovery_already_completed=(
                                    safety_recovery_already_completed
                                ),
                            )

                    execution_task = asyncio.create_task(execute_turn())
                    lease_task = asyncio.create_task(
                        self._maintain_command_lease(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            idempotency_key=idempotency_key,
                            lease_owner=lease_owner,
                        )
                    )
                    done, _ = await asyncio.wait(
                        {execution_task, lease_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if lease_task in done:
                        lease_error = lease_task.exception()
                        execution_task.cancel()
                        try:
                            response = await execution_task
                        except asyncio.CancelledError:
                            if lease_error is not None and crisis_turn is not None:
                                response = _degraded_crisis_response(crisis_turn)
                            elif lease_error is not None:
                                raise lease_error
                            else:
                                raise ConversationLeaseLostError(
                                    "conversation command lease maintenance stopped"
                                )
                        if response.crisis_persistence_status != (
                            "degraded_retry_required"
                        ):
                            if lease_error is not None:
                                raise lease_error
                            raise ConversationLeaseLostError(
                                "conversation command lease maintenance stopped"
                            )
                    else:
                        response = await execution_task
                if response.crisis_persistence_status == "degraded_retry_required":
                    try:
                        await self._repository.release_command_lease(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            idempotency_key=idempotency_key,
                            lease_owner=lease_owner,
                        )
                    except Exception as exc:
                        logger.error(
                            "Crisis command release failed: %s",
                            type(exc).__name__,
                        )
                    mark_first_turn_output()
                    return response
                try:
                    await self._repository.assert_command_lease(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        idempotency_key=idempotency_key,
                        lease_owner=lease_owner,
                    )
                except BaseException as exc:
                    _reraise_external_cancellation(exc)
                    if response.crisis_persistence_status != "persisted":
                        raise
                    response.crisis_persistence_status = (
                        "safety_state_persisted_command_unconfirmed"
                    )
                    with suppress(Exception):
                        await self._repository.release_command_lease(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            idempotency_key=idempotency_key,
                            lease_owner=lease_owner,
                        )
                    mark_first_turn_output()
                    return response
                with measure_turn_stage("command_complete"):
                    try:
                        await self._repository.complete_command(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            idempotency_key=idempotency_key,
                            lease_owner=lease_owner,
                            result=response.model_dump_json(),
                        )
                    except BaseException as exc:
                        _reraise_external_cancellation(exc)
                        if response.crisis_persistence_status != "persisted":
                            raise
                        response.crisis_persistence_status = (
                            "safety_state_persisted_command_unconfirmed"
                        )
                        with suppress(Exception):
                            await self._repository.release_command_lease(
                                conversation_id=conversation_id,
                                user_id=user_id,
                                idempotency_key=idempotency_key,
                                lease_owner=lease_owner,
                            )
                mark_first_turn_output()
                return response
            except BaseException as exc:
                with suppress(Exception):
                    await self._repository.release_command_lease(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        idempotency_key=idempotency_key,
                        lease_owner=lease_owner,
                    )
                _reraise_external_cancellation(exc)
                if crisis_turn is not None:
                    logger.error(
                        "Claimed crisis turn execution failed before safe completion: %s",
                        type(exc).__name__,
                    )
                    mark_first_turn_output()
                    return _degraded_crisis_response(crisis_turn)
                raise
            finally:
                for task in (execution_task, lease_task):
                    if task is not None and not task.done():
                        task.cancel()
                for task in (execution_task, lease_task):
                    if task is not None:
                        # The primary await above has already propagated or
                        # deliberately converted the task outcome. Cleanup
                        # must not re-raise that same observed exception and
                        # override a crisis fallback response.
                        with suppress(asyncio.CancelledError, Exception):
                            await task
        finally:
            report = recorder.finish()
            reset_turn_latency(latency_token)
            if self._latency_sink is not None:
                try:
                    self._latency_sink(report)
                except Exception as exc:
                    logger.warning(
                        "Conversation latency sink failed: %s",
                        exc.__class__.__name__,
                    )

    async def _maintain_command_lease(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
        lease_owner: str,
    ) -> None:
        """Renew a command lease while its potentially long turn is running."""
        interval = max(0.1, min(5.0, self._command_lease_seconds / 3))
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self._repository.renew_command_lease(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    lease_owner=lease_owner,
                    lease_seconds=self._command_lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ConversationLeaseLostError(
                    "conversation command lease renewal failed"
                ) from exc
            if not renewed:
                raise ConversationLeaseLostError(
                    "conversation command lease was lost"
                )

    async def _unclaimed_crisis_turn(
        self,
        *,
        conversation: Conversation,
        message: str,
        idempotency_key: str,
        lease_owner: str,
        safety_result: SafetyResult,
        streaming_requested: bool,
        response_chunk_sink: ResponseChunkSink | None,
    ) -> ConversationTurn:
        """Build a transient crisis response after owner validation but before claim."""
        try:
            stack = await self._repository.list_module_stack(
                conversation_id=conversation.conversation_id,
                user_id=conversation.user_id,
            )
        except BaseException as exc:
            _reraise_external_cancellation(exc)
            logger.error(
                "Transient crisis module state read failed: %s",
                type(exc).__name__,
            )
            stack = []
        return ConversationTurn(
            conversation=conversation,
            conversation_id=conversation.conversation_id,
            user_id=conversation.user_id,
            message=message,
            idempotency_key=idempotency_key,
            lease_owner=lease_owner,
            stack=stack,
            safety_result=safety_result,
            user_event=None,
            intent_plan=None,
            streaming_requested=streaming_requested,
            response_chunk_sink=response_chunk_sink,
            crisis_persistence_failed=True,
        )

    async def _execute_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        idempotency_key: str,
        lease_owner: str,
        streaming_requested: bool,
        response_chunk_sink: ResponseChunkSink | None,
        classification_override: TurnClassification | None = None,
        on_crisis_identified: Callable[[ConversationTurn], None] | None = None,
        safety_recovery_already_completed: bool = False,
    ) -> ConversationMessageResponse:
        """Dispatch one claimed turn through ordered, typed handlers."""
        with measure_turn_stage("turn_preparation"):
            turn = await self._start_turn(
                conversation_id=conversation_id,
                user_id=user_id,
                message=message,
                idempotency_key=idempotency_key,
                lease_owner=lease_owner,
                streaming_requested=streaming_requested,
                response_chunk_sink=response_chunk_sink,
                classification_override=classification_override,
                on_crisis_identified=on_crisis_identified,
                safety_recovery_already_completed=(
                    safety_recovery_already_completed
                ),
            )
        if turn.safety_result.risk_level == RiskLevel.CRISIS:
            with measure_turn_stage("turn_dispatch"):
                return await self._handle_crisis_turn(turn)
        with measure_turn_stage("turn_dispatch"):
            return await self._handle_non_crisis_turn(turn)

    async def _start_turn(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        idempotency_key: str,
        lease_owner: str,
        streaming_requested: bool,
        response_chunk_sink: ResponseChunkSink | None,
        classification_override: TurnClassification | None = None,
        on_crisis_identified: Callable[[ConversationTurn], None] | None = None,
        safety_recovery_already_completed: bool = False,
    ) -> ConversationTurn:
        """Load and append the authoritative input state for one claimed turn."""
        with measure_turn_stage("state_load"):
            conversation = await self._repository.get_for_user(
                conversation_id,
                user_id,
            )
        if conversation is None:
            raise LookupError("conversation not found")

        with measure_turn_stage("state_load"):
            stack = await self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        active_run = stack[-1] if stack else None
        classification = (
            classification_override
            if classification_override is not None
            else await self._turn_classifier.classify(message)
        )
        safety_result = classification.safety_result
        crisis_persistence_failed = False
        try:
            await self._repository.record_command_safety_classification(
                conversation_id=conversation_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                lease_owner=lease_owner,
                crisis=safety_result.risk_level is RiskLevel.CRISIS,
            )
        except BaseException as exc:
            _reraise_external_cancellation(exc)
            if safety_result.risk_level is not RiskLevel.CRISIS:
                raise
            crisis_persistence_failed = True
            logger.error(
                "Crisis command classification persistence failed: %s",
                type(exc).__name__,
            )
        if (
            safety_result.risk_level is RiskLevel.CRISIS
            and on_crisis_identified is not None
        ):
            on_crisis_identified(
                ConversationTurn(
                    conversation=conversation,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    message=message,
                    idempotency_key=idempotency_key,
                    lease_owner=lease_owner,
                    stack=stack,
                    safety_result=safety_result,
                    user_event=None,
                    intent_plan=None,
                    streaming_requested=streaming_requested,
                    response_chunk_sink=response_chunk_sink,
                    intent_result=classification.intent_result,
                    safety_recovery_already_completed=(
                        safety_recovery_already_completed
                    ),
                )
            )
        try:
            with measure_turn_stage("input_persistence"):
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
        except BaseException as exc:
            _reraise_external_cancellation(exc)
            if safety_result.risk_level != RiskLevel.CRISIS:
                raise
            user_event = None
            crisis_persistence_failed = True
            logger.error("Crisis input persistence failed: %s", type(exc).__name__)
        try:
            with measure_turn_stage("state_load"):
                intent_plan = await self._repository.get_intent_plan(
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
        except BaseException as exc:
            _reraise_external_cancellation(exc)
            if safety_result.risk_level != RiskLevel.CRISIS:
                raise
            intent_plan = None
            crisis_persistence_failed = True
            logger.error("Crisis intent state load failed: %s", type(exc).__name__)
        turn = ConversationTurn(
            conversation=conversation,
            conversation_id=conversation_id,
            user_id=user_id,
            message=message,
            idempotency_key=idempotency_key,
            lease_owner=lease_owner,
            stack=stack,
            safety_result=safety_result,
            user_event=user_event,
            intent_plan=intent_plan,
            streaming_requested=streaming_requested,
            response_chunk_sink=response_chunk_sink,
            intent_result=classification.intent_result,
            crisis_persistence_failed=crisis_persistence_failed,
            safety_recovery_already_completed=(
                safety_recovery_already_completed
            ),
        )
        if safety_result.risk_level is RiskLevel.CRISIS and on_crisis_identified:
            on_crisis_identified(turn)
        return turn

    async def _handle_crisis_turn(
        self,
        turn: ConversationTurn,
    ) -> ConversationMessageResponse:
        """Preempt ordinary state without invoking context, memory, or generation."""
        response = crisis_escalation_response(paused_activity="当前对话和练习")
        appended_events = [turn.user_event] if turn.user_event is not None else []
        persistence_failed = turn.crisis_persistence_failed
        if not turn.safety_recovery_already_completed:
            try:
                appended_events.extend(
                    await self._module_coordinator.preempt_for_crisis(
                        conversation_id=turn.conversation_id,
                        user_id=turn.user_id,
                    )
                )
            except BaseException as exc:
                _reraise_external_cancellation(exc)
                persistence_failed = True
                logger.error(
                    "Crisis module preemption persistence failed: %s",
                    type(exc).__name__,
                )
            try:
                if turn.intent_plan is not None:
                    cancelled_plan, _ = apply_intent_queue_control(
                        turn.intent_plan,
                        IntentQueueControl(action=IntentQueueControlAction.CANCEL_ALL),
                    )
                    if cancelled_plan is not turn.intent_plan:
                        turn.intent_plan = await self._repository.update_intent_plan(
                            cancelled_plan,
                            expected_version=turn.intent_plan.version,
                        )
            except BaseException as exc:
                _reraise_external_cancellation(exc)
                persistence_failed = True
                logger.error(
                    "Crisis intent cancellation persistence failed: %s",
                    type(exc).__name__,
                )
        try:
            crisis_event = await self._repository.get_event_by_idempotency(
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                idempotency_key=f"crisis:{turn.idempotency_key}",
            )
            if crisis_event is None and turn.safety_recovery_already_completed:
                raise ConversationConcurrencyError(
                    "completed crisis safety recovery has no durable event"
                )
            if crisis_event is None:
                crisis_event = await self._repository.append_event(
                    conversation_id=turn.conversation_id,
                    user_id=turn.user_id,
                    event_type=ConversationEventType.CRISIS_ESCALATED,
                    role=ConversationEventRole.SYSTEM,
                    content=response,
                    structured_payload=CrisisEscalatedEventPayload(),
                    module_run_id=(
                        turn.active_run.module_run_id if turn.active_run else None
                    ),
                    parent_module_run_id=(
                        turn.active_run.parent_module_run_id if turn.active_run else None
                    ),
                    idempotency_key=f"crisis:{turn.idempotency_key}",
                )
            appended_events.append(crisis_event)
        except BaseException as exc:
            _reraise_external_cancellation(exc)
            persistence_failed = True
            logger.error("Crisis escalation event persistence failed: %s", type(exc).__name__)

        diagnostics = _crisis_bypass_diagnostics(turn.conversation_id)
        if persistence_failed:
            return _degraded_crisis_response(
                turn,
                appended_events=appended_events,
            )
        if not turn.safety_recovery_already_completed:
            try:
                await self._repository.complete_command_safety_recovery(
                    conversation_id=turn.conversation_id,
                    user_id=turn.user_id,
                    idempotency_key=turn.idempotency_key,
                    lease_owner=turn.lease_owner,
                )
            except BaseException as exc:
                _reraise_external_cancellation(exc)
                logger.error(
                    "Crisis command recovery completion failed: %s",
                    type(exc).__name__,
                )
                return _degraded_crisis_response(
                    turn,
                    appended_events=appended_events,
                )
        try:
            return await self._response(
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                appended_events=appended_events,
                response=response,
                safety_result=turn.safety_result,
                context_diagnostics=diagnostics,
                intent_plan=turn.intent_plan,
                crisis_persistence_status="persisted",
            )
        except BaseException as exc:
            _reraise_external_cancellation(exc)
            logger.error("Crisis response state read failed: %s", type(exc).__name__)
            return _degraded_crisis_response(
                turn,
                appended_events=appended_events,
                # The crisis event and all preceding safety-state writes were
                # confirmed.  Only the read used to enrich the response failed;
                # command completion can still durably capture this safe result.
                persistence_status="persisted",
            )

    async def _handle_non_crisis_turn(
        self,
        turn: ConversationTurn,
    ) -> ConversationMessageResponse:
        """Dispatch a safe turn through queue, intent, and fallback handlers."""
        queue_resolution = await self._intent_queue_coordinator.handle(turn)
        if queue_resolution is not None:
            return await self._resolve_turn(turn, queue_resolution)

        dispatch = await self._intent_dispatch_coordinator.handle(turn)
        if dispatch.resolution is not None:
            return await self._resolve_turn(turn, dispatch.resolution)
        if turn.stack:
            return await self._handle_active_module_turn(turn)
        return await self._handle_general_turn(turn, dispatch.intent_result)

    async def _resolve_turn(
        self,
        turn: ConversationTurn,
        resolution: TurnResolution,
    ) -> ConversationMessageResponse:
        """Render one coordinator result through the shared response builder."""
        return await self._response(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            appended_events=resolution.appended_events,
            response=resolution.response,
            safety_result=turn.safety_result,
            context_diagnostics=resolution.context_diagnostics,
            pending_proposal=resolution.pending_proposal,
            intent_plan=turn.intent_plan,
        )

    async def _handle_active_module_turn(
        self,
        turn: ConversationTurn,
    ) -> ConversationMessageResponse:
        """Execute a message against the active top module frame."""
        active_run = turn.active_run
        if active_run is None:
            raise ConversationStateError("active module turn has no module frame")
        context = await self._assemble_module_context(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            message=turn.message,
            module_type=active_run.module_type,
            module_parameters=active_run.module_parameters,
            current_event_id=turn.required_user_event.event_id,
        )
        result, module_event, context = await self._module_coordinator.handle_message(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            message=turn.message,
            idempotency_key=turn.idempotency_key,
            context=context,
        )
        completion_events: list[ConversationEvent] = []
        progression_responses: list[str] = []
        if (
            turn.intent_plan is not None
            and isinstance(result.event_payload, WorksheetMessageEventPayload)
            and result.event_payload.completed
        ):
            ended = await self._module_coordinator.terminate_current(
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
                module_run_id=active_run.module_run_id,
                context=context,
            )
            completion_events.extend(ended.appended_events)
            source_event = await self._repository.get_event_for_user(
                event_id=turn.intent_plan.source_event_id,
                conversation_id=turn.conversation_id,
                user_id=turn.user_id,
            )
            if source_event is not None:
                original_safety = await self._safety_classifier.classify(
                    source_event.content
                )
                (
                    turn.intent_plan,
                    progression_events,
                    progression_responses,
                ) = await self._complete_and_consume_intent_plan(
                    plan=turn.intent_plan,
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
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            appended_events=[turn.required_user_event, module_event, *completion_events],
            response=combined_response,
            safety_result=turn.safety_result,
            context_diagnostics=context.diagnostics,
            intent_plan=turn.intent_plan,
        )

    async def _handle_general_turn(
        self,
        turn: ConversationTurn,
        intent_result: IntentResult,
    ) -> ConversationMessageResponse:
        """Generate and persist an ordinary non-module assistant response."""
        assistant_key = f"assistant:{turn.idempotency_key}"
        context = await self._context_manager.assemble(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            current_user_message=turn.message,
            current_event_id=turn.required_user_event.event_id,
        )
        replay = await self._repository.get_event_by_idempotency(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            idempotency_key=assistant_key,
        )
        workflow_response = None
        if replay is None:
            harness_kwargs = {
                "trusted_safety_result": turn.safety_result,
                "trusted_intent_result": intent_result,
                "trusted_conversation_context": build_conversation_prompt_context(
                    context
                ),
                "streaming_requested": turn.streaming_requested,
            }
            if turn.response_chunk_sink is not None:
                harness_kwargs["response_chunk_sink"] = turn.response_chunk_sink
            with measure_turn_stage("workflow"):
                workflow_response = await self._harness.run(
                    ChatRequest(
                        user_id=turn.user_id,
                        message=turn.message,
                        context=conversation_workflow_context(
                            context,
                            source_event_id=turn.required_user_event.event_id,
                        ),
                    ),
                    **harness_kwargs,
                )
            with measure_turn_stage("response_persistence"):
                replay = await self._repository.append_event(
                    conversation_id=turn.conversation_id,
                    user_id=turn.user_id,
                    event_type=ConversationEventType.ASSISTANT_MESSAGE,
                    role=ConversationEventRole.ASSISTANT,
                    content=workflow_response.response,
                    idempotency_key=assistant_key,
                )
        progression_events: list[ConversationEvent] = []
        progression_responses: list[str] = []
        if turn.intent_plan is not None:
            (
                turn.intent_plan,
                progression_events,
                progression_responses,
            ) = await self._complete_and_consume_intent_plan(
                plan=turn.intent_plan,
                completed_intent=intent_result.intent,
                source_event=turn.required_user_event,
                safety_result=turn.safety_result,
                context=context,
            )
        combined_response = replay.content
        if progression_responses:
            combined_response += "\n\n接下来：" + "\n\n".join(
                progression_responses
            )
        return await self._response(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            appended_events=[turn.required_user_event, replay, *progression_events],
            response=combined_response,
            safety_result=turn.safety_result,
            context_diagnostics=context.diagnostics,
            workflow_response=workflow_response,
            intent_plan=turn.intent_plan,
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
        await self._assert_no_unfinished_safety_recovery(
            conversation_id=conversation_id,
            user_id=user_id,
        )
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
        await self._assert_no_unfinished_safety_recovery(
            conversation_id=conversation_id,
            user_id=user_id,
        )
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
        await self._assert_no_unfinished_safety_recovery(
            conversation_id=job.conversation_id,
            user_id=job.user_id,
        )
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
        await self._assert_no_unfinished_safety_recovery(
            conversation_id=conversation_id,
            user_id=user_id,
        )
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
        await self._assert_no_unfinished_safety_recovery(
            conversation_id=conversation_id,
            user_id=user_id,
        )
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

    async def _assert_no_unfinished_safety_recovery(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Fence ordinary module control while a crisis obligation is pending."""
        if await self._repository.has_unfinished_command_safety_recovery(
            conversation_id=conversation_id,
            user_id=user_id,
        ):
            raise ConversationCommandInProgressError(
                "conversation crisis safety recovery is still pending"
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
        """Delegate bounded sequential execution to the intent step runner."""
        result = await self._intent_step_runner.run(
            plan=plan,
            source_event=source_event,
            safety_result=safety_result,
            context=context,
        )
        return result.plan, result.events, result.responses

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
        crisis_persistence_status: str = "not_applicable",
    ) -> ConversationMessageResponse:
        with measure_turn_stage("response_assembly"):
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
                crisis_persistence_status=crisis_persistence_status,
            )


def _crisis_bypass_diagnostics(
    conversation_id: str,
) -> ConversationContextDiagnostics:
    """Describe the intentional no-context crisis path without user content."""
    return ConversationContextDiagnostics(
        conversation_id_hash=conversation_id_hash(conversation_id),
        recent_event_count=0,
        active_module_count=0,
        selected_memory_count=0,
        estimated_tokens=0,
        total_token_budget=1,
        budget_profile=ConversationContextProfile.ORDINARY,
        dropped_sections=["crisis_bypass"],
        tokenizer_backend="not_used",
        context_backend="crisis_bypass",
        cache_status="bypassed",
    )


def _degraded_crisis_response(
    turn: ConversationTurn,
    *,
    appended_events: list[ConversationEvent] | None = None,
    persistence_status: str = "degraded_retry_required",
) -> ConversationMessageResponse:
    """Return static safety guidance while accurately flagging unconfirmed state."""
    return ConversationMessageResponse(
        conversation=turn.conversation,
        appended_events=appended_events or [],
        active_module_stack=turn.stack,
        intent_plan=turn.intent_plan,
        response=crisis_escalation_response(paused_activity="当前对话和练习"),
        safety_result=turn.safety_result,
        context_diagnostics=_crisis_bypass_diagnostics(turn.conversation_id),
        crisis_persistence_status=persistence_status,
    )


def _reraise_external_cancellation(exc: BaseException) -> None:
    """Preserve real task cancellation while allowing injected I/O cancellation fallback."""
    if not isinstance(exc, asyncio.CancelledError):
        return
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise exc


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
