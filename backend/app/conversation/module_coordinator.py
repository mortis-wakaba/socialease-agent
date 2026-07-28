"""Coordinate confirmed module frames and domain-service adapters."""

from datetime import UTC, datetime
from hashlib import sha256
import logging

from app.conversation.adapters import (
    ModuleAdapter,
    ModuleAdapterResult,
    PreparedModuleStart,
)
from app.conversation.module_overlay_store import (
    ModuleOverlayStore,
    create_module_overlay_store,
)
from app.conversation.module_policy import ModuleStackPolicy
from app.conversation.repository import (
    ConversationConcurrencyError,
    ConversationRepository,
)
from app.memory.task_state_store import TaskStateStoreUnavailable
from app.models_conversation import (
    ConversationEvent,
    ConversationEventRole,
    ConversationEventType,
    ModuleLifecycleEventPayload,
    ModuleProposal,
    ModuleProposalStatus,
    ModuleRun,
    ModuleRunStatus,
    ModuleType,
    ExposureParameters,
)
from app.models_conversation_api import ModuleControlResponse
from app.models_conversation_context import ConversationWorkingContext
from app.models_module_overlay import ModuleOverlay


logger = logging.getLogger(__name__)


def _initial_domain_session_id(proposal: ModuleProposal) -> str | None:
    """Reserve a deterministic domain id for replayable module startup."""
    if proposal.proposed_module == ModuleType.EXPOSURE:
        parameters = proposal.bounded_parameters
        if (
            isinstance(parameters, ExposureParameters)
            and parameters.starting_anxiety is None
        ):
            return None
    return _module_run_id(proposal.proposal_id)


class ModuleCoordinator:
    """Own stack transitions while domain services own module behavior."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        adapters: dict[ModuleType, ModuleAdapter],
        overlay_store: ModuleOverlayStore | None = None,
    ) -> None:
        self._repository = repository
        self._adapters = adapters
        self._overlay_store = overlay_store or create_module_overlay_store()

    async def accept(
        self,
        proposal: ModuleProposal,
        context: ConversationWorkingContext,
    ) -> ModuleControlResponse:
        """Consume one proposal and push its confirmed module frame."""
        module_run_id = _module_run_id(proposal.proposal_id)
        existing = await self._repository.get_module_run_for_user(
            module_run_id=module_run_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
        )
        if existing is not None:
            if await self._repository.claim_module_start(
                module_run_id=existing.module_run_id
            ):
                return await self._reconcile_module_start(
                    run=existing,
                    context=context,
                    proposal_id=proposal.proposal_id,
                )
            return await self._control_response(
                conversation_id=proposal.conversation_id,
                user_id=proposal.user_id,
                events=[],
                response="这个模块已经进入当前会话。",
            )

        stack = await self._repository.list_module_stack(
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
        )
        ModuleStackPolicy.validate_push(stack, proposal.proposed_module)
        now = datetime.now(UTC)
        run = ModuleRun(
            module_run_id=module_run_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
            module_type=proposal.proposed_module,
            source_event_id=proposal.source_event_id,
            parent_module_run_id=(
                stack[-1].module_run_id if stack else None
            ),
            depth=len(stack) + 1,
            module_parameters=proposal.bounded_parameters,
            domain_session_id=_initial_domain_session_id(proposal),
            started_at=now,
        )
        parent = stack[-1] if stack else None
        prepared = await self._adapter(run.module_type).prepare_start(run, context)
        try:
            async with self._repository.module_start_transaction():
                run = await self._repository.begin_module_start(
                    proposal=proposal,
                    run=run,
                    parent=parent,
                )
                if not await self._repository.claim_module_start(
                    module_run_id=run.module_run_id
                ):
                    raise ConversationConcurrencyError(
                        "module startup could not be claimed"
                    )
                run, result, parent = await self._start_domain_session(
                    run=run,
                    prepared=prepared,
                    parent=parent,
                )
        except Exception as exc:
            await self._repository.retry_module_start(
                module_run_id=run.module_run_id,
                error_code=exc.__class__.__name__,
            )
            raise
        return await self._finalize_module_start(
            run=run,
            prepared=prepared,
            result=result,
            parent=parent,
            context=context,
            proposal_id=proposal.proposal_id,
        )

    async def reconcile_claimed(
        self,
        *,
        run: ModuleRun,
        context: ConversationWorkingContext,
        proposal_id: str,
    ) -> ModuleControlResponse:
        """Reconcile a job already leased by the background worker."""
        return await self._reconcile_module_start(
            run=run,
            context=context,
            proposal_id=proposal_id,
        )

    async def _reconcile_module_start(
        self,
        *,
        run: ModuleRun,
        context: ConversationWorkingContext,
        proposal_id: str,
    ) -> ModuleControlResponse:
        """Recreate a missing domain session and finalize its durable outbox."""
        try:
            prepared = await self._adapter(run.module_type).prepare_start(
                run, context
            )
            async with self._repository.module_start_transaction():
                run, result, parent = await self._start_domain_session(
                    run=run,
                    prepared=prepared,
                )
        except Exception as exc:
            await self._repository.retry_module_start(
                module_run_id=run.module_run_id,
                error_code=exc.__class__.__name__,
            )
            raise
        return await self._finalize_module_start(
            run=run,
            prepared=prepared,
            result=result,
            parent=parent,
            context=context,
            proposal_id=proposal_id,
        )

    async def _start_domain_session(
        self,
        *,
        run: ModuleRun,
        prepared: PreparedModuleStart,
        parent: ModuleRun | None = None,
    ) -> tuple[ModuleRun, ModuleAdapterResult, ModuleRun | None]:
        """Persist the module's internal domain state in the bound transaction."""
        if parent is None and run.parent_module_run_id is not None:
            parent = await self._repository.get_module_run_for_user(
                module_run_id=run.parent_module_run_id,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
            )
            if parent is None:
                raise LookupError("parent module run not found")
        if parent is not None:
            await self._adapter(parent.module_type).suspend(parent)
        result = await self._adapter(run.module_type).persist_start(run, prepared)
        if result.domain_session_id != run.domain_session_id:
            if result.domain_session_id is None:
                raise ValueError("module startup lost its reserved session id")
            run = await self._repository.update_module_domain_session(
                module_run_id=run.module_run_id,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                expected_version=run.version,
                domain_session_id=result.domain_session_id,
            )
        return run, result, parent

    async def _finalize_module_start(
        self,
        *,
        run: ModuleRun,
        prepared: PreparedModuleStart,
        result: ModuleAdapterResult,
        parent: ModuleRun | None,
        context: ConversationWorkingContext,
        proposal_id: str,
    ) -> ModuleControlResponse:
        """Publish projections and idempotent events after the database commits."""
        try:
            await self._adapter(run.module_type).after_start_commit(
                run,
                prepared=prepared,
                result=result,
            )
            events: list[ConversationEvent] = []
            if parent is not None:
                await self._refresh_overlay(parent, context)
                events.append(
                    await self._append_lifecycle_event(
                        run=parent,
                        event_type=ConversationEventType.MODULE_SUSPENDED,
                        content="父模块已暂停，等待子模块结束后恢复。",
                        idempotency_key=f"module-suspended:{proposal_id}",
                    )
                )
            await self._refresh_overlay(run, context)
            events.append(
                await self._append_lifecycle_event(
                    run=run,
                    event_type=ConversationEventType.MODULE_STARTED,
                    content=f"已进入 {run.module_type.value} 模块。",
                    idempotency_key=f"module-started:{proposal_id}",
                )
            )
            events.append(
                await self._append_result_event(
                    run=run,
                    result=result,
                    idempotency_key=f"module-opening:{proposal_id}",
                )
            )
            await self._repository.complete_module_start(
                module_run_id=run.module_run_id
            )
        except Exception as exc:
            await self._repository.retry_module_start(
                module_run_id=run.module_run_id,
                error_code=exc.__class__.__name__,
            )
            raise
        return await self._control_response(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            events=events,
            response=result.response,
        )

    async def handle_message(
        self,
        *,
        conversation_id: str,
        user_id: str,
        message: str,
        idempotency_key: str,
        context: ConversationWorkingContext,
    ) -> tuple[ModuleAdapterResult, ConversationEvent]:
        """Send one message to the active top module frame."""
        stack = await self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if not stack:
            raise LookupError("active module not found")
        run = stack[-1]
        response_key = f"module-response:{idempotency_key}"
        replay = await self._repository.get_event_by_idempotency(
            conversation_id=conversation_id,
            user_id=user_id,
            idempotency_key=response_key,
        )
        if replay is not None:
            if replay.structured_payload is None:
                raise ValueError("module replay event has no typed payload")
            return (
                ModuleAdapterResult(
                    response=replay.content,
                    domain_session_id=run.domain_session_id,
                    event_payload=replay.structured_payload,
                ),
                replay,
            )
        projected_context = await self.project_context(
            context.model_copy(update={"active_module_stack": stack})
        )
        overlay = projected_context.active_module_overlay
        if overlay is None or overlay.module_run_id != run.module_run_id:
            raise LookupError("active module overlay not found")
        result = await self._adapter(run.module_type).handle_message(
            run,
            message,
            projected_context,
            overlay,
        )
        if (
            result.domain_session_id is not None
            and result.domain_session_id != run.domain_session_id
        ):
            run = await self._repository.update_module_domain_session(
                module_run_id=run.module_run_id,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                expected_version=run.version,
                domain_session_id=result.domain_session_id,
            )
        else:
            run = await self._repository.advance_module_run_version(
                module_run_id=run.module_run_id,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                expected_version=run.version,
            )
        await self._refresh_overlay(run, projected_context)
        event = await self._append_result_event(
            run=run,
            result=result,
            idempotency_key=response_key,
        )
        return result, event

    async def terminate_current(
        self,
        *,
        conversation_id: str,
        user_id: str,
        module_run_id: str,
        context: ConversationWorkingContext | None = None,
    ) -> ModuleControlResponse:
        """Terminate only the top frame and resume its parent."""
        stack = await self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if not stack or stack[-1].module_run_id != module_run_id:
            existing = await self._repository.get_module_run_for_user(
                module_run_id=module_run_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if existing is not None and existing.status == ModuleRunStatus.TERMINATED:
                return await self._control_response(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    events=[],
                    response="当前模块已经结束。",
                )
            raise LookupError("active top module not found")
        top = stack[-1]
        await self._adapter(top.module_type).terminate(top)
        ended_at = datetime.now(UTC)
        terminated = await self._repository.transition_module_run(
            module_run_id=top.module_run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            expected_status=ModuleRunStatus.ACTIVE,
            expected_version=top.version,
            target_status=ModuleRunStatus.TERMINATED,
            ended_at=ended_at,
        )
        if terminated is None:
            raise LookupError("module run not found")
        await self._overlay_store.delete(terminated)
        events = [
            await self._append_lifecycle_event(
                run=terminated,
                event_type=ConversationEventType.MODULE_TERMINATED,
                content="用户已结束当前模块。",
                idempotency_key=f"module-terminated:{top.module_run_id}",
            )
        ]
        response = "已结束当前模块，返回普通对话。"
        if len(stack) > 1:
            parent = stack[-2]
            await self._adapter(parent.module_type).resume(parent)
            resumed = await self._repository.transition_module_run(
                module_run_id=parent.module_run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                expected_status=ModuleRunStatus.SUSPENDED,
                expected_version=parent.version,
                target_status=ModuleRunStatus.ACTIVE,
                ended_at=None,
            )
            if resumed is None:
                raise LookupError("parent module run not found")
            await self._refresh_overlay(resumed, context)
            events.append(
                await self._append_lifecycle_event(
                    run=resumed,
                    event_type=ConversationEventType.MODULE_RESUMED,
                    content="已恢复父模块。",
                    idempotency_key=f"module-resumed:{top.module_run_id}",
                )
            )
            response = "已结束当前模块，并恢复上一层练习。"
        await self._set_active_depth(
            conversation_id=conversation_id,
            user_id=user_id,
            depth=max(0, len(stack) - 1),
        )
        return await self._control_response(
            conversation_id=conversation_id,
            user_id=user_id,
            events=events,
            response=response,
        )

    async def terminate_all(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ModuleControlResponse:
        """Terminate every frame from child to parent without resuming."""
        stack = await self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        events: list[ConversationEvent] = []
        for run in reversed(stack):
            await self._adapter(run.module_type).terminate(run)
            terminated = await self._repository.transition_module_run(
                module_run_id=run.module_run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                expected_status=run.status,
                expected_version=run.version,
                target_status=ModuleRunStatus.TERMINATED,
                ended_at=datetime.now(UTC),
            )
            if terminated is None:
                continue
            await self._overlay_store.delete(terminated)
            events.append(
                await self._append_lifecycle_event(
                    run=terminated,
                    event_type=ConversationEventType.MODULE_TERMINATED,
                    content="用户已结束全部模块。",
                    idempotency_key=(
                        f"module-terminated-all:{run.module_run_id}"
                    ),
                )
            )
        await self._set_active_depth(
            conversation_id=conversation_id,
            user_id=user_id,
            depth=0,
        )
        return await self._control_response(
            conversation_id=conversation_id,
            user_id=user_id,
            events=events,
            response="已结束全部模块，返回普通对话。",
        )

    async def preempt_for_crisis(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ConversationEvent]:
        """Stop every frame without letting adapter failure delay safety."""
        stack = await self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        events: list[ConversationEvent] = []
        for run in reversed(stack):
            try:
                await self._adapter(run.module_type).terminate(run)
            except Exception:
                # Durable safety state must not depend on optional runtime state.
                logger.exception(
                    "Module runtime termination failed during crisis preemption",
                    extra={
                        "module_type": run.module_type.value,
                        "module_run_id_hash": sha256(
                            run.module_run_id.encode("utf-8")
                        ).hexdigest()[:16],
                    },
                )
            terminated = await self._repository.transition_module_run(
                module_run_id=run.module_run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                expected_status=run.status,
                expected_version=run.version,
                target_status=ModuleRunStatus.TERMINATED,
                ended_at=datetime.now(UTC),
            )
            if terminated is None:
                continue
            await self._overlay_store.delete(terminated)
            events.append(
                await self._append_lifecycle_event(
                    run=terminated,
                    event_type=ConversationEventType.MODULE_TERMINATED,
                    content="安全升级已停止当前模块。",
                    idempotency_key=(
                        f"crisis-module-terminated:{run.module_run_id}:"
                        f"{run.version}"
                    ),
                )
            )
        if stack:
            await self._set_active_depth(
                conversation_id=conversation_id,
                user_id=user_id,
                depth=0,
            )
        return events

    async def delete_runtime_contexts(self, runs: list[ModuleRun]) -> None:
        """Delete short-lived adapter state before durable conversation deletion."""
        for run in runs:
            try:
                await self._adapter(run.module_type).delete_runtime_context(run)
            except TaskStateStoreUnavailable:
                logger.warning(
                    "Module runtime cache unavailable during durable deletion",
                    extra={"module_type": run.module_type.value},
                )
            await self._overlay_store.delete(run)

    async def delete_user_cache(self, *, user_id: str) -> int:
        """Delete every cached overlay for one owner."""
        return await self._overlay_store.delete_user(user_id=user_id)

    async def project_context(
        self,
        context: ConversationWorkingContext,
    ) -> ConversationWorkingContext:
        """Attach one active overlay and bounded suspended-parent projections."""
        stack = context.active_module_stack
        if not stack:
            return context
        overlays = [
            await self._load_overlay(run, context)
            for run in stack
        ]
        parents = [
            self._adapter(run.module_type).project_for_parent_resume(overlay)
            for run, overlay in zip(stack[:-1], overlays[:-1], strict=True)
        ]
        active = overlays[-1]
        diagnostics = context.diagnostics.model_copy(
            update={
                "active_overlay_type": active.module_type.value,
                "active_overlay_version": active.version,
                "parent_resume_projection_count": len(parents),
            }
        )
        return context.model_copy(
            update={
                "active_module_overlay": active,
                "parent_resume_projections": parents,
                "diagnostics": diagnostics,
            }
        )

    async def close(self) -> None:
        """Close the shared overlay cache client."""
        await self._overlay_store.close()

    async def health(self) -> bool:
        """Return whether the configured overlay cache responds."""
        return await self._overlay_store.health()

    async def _load_overlay(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext,
    ) -> ModuleOverlay:
        overlay = await self._overlay_store.get(run)
        if overlay is not None:
            return overlay
        return await self._refresh_overlay(run, context)

    async def _refresh_overlay(
        self,
        run: ModuleRun,
        context: ConversationWorkingContext | None = None,
    ) -> ModuleOverlay:
        overlay = await self._adapter(run.module_type).build_overlay(
            run,
            context,
        )
        await self._overlay_store.put(run, overlay)
        return overlay

    def _adapter(self, module_type: ModuleType) -> ModuleAdapter:
        adapter = self._adapters.get(module_type)
        if adapter is None:
            raise ValueError(f"module adapter is unavailable: {module_type.value}")
        return adapter

    async def _append_lifecycle_event(
        self,
        *,
        run: ModuleRun,
        event_type: ConversationEventType,
        content: str,
        idempotency_key: str,
    ) -> ConversationEvent:
        return await self._repository.append_event(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            event_type=event_type,
            role=ConversationEventRole.SYSTEM,
            content=content,
            structured_payload=ModuleLifecycleEventPayload(
                module_run_id=run.module_run_id,
                module_type=run.module_type,
                parent_module_run_id=run.parent_module_run_id,
            ),
            module_run_id=run.module_run_id,
            parent_module_run_id=run.parent_module_run_id,
            idempotency_key=idempotency_key,
        )

    async def _append_result_event(
        self,
        *,
        run: ModuleRun,
        result: ModuleAdapterResult,
        idempotency_key: str,
    ) -> ConversationEvent:
        return await self._repository.append_event(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            event_type=ConversationEventType.MODULE_MESSAGE,
            role=ConversationEventRole.ASSISTANT,
            content=result.response,
            structured_payload=result.event_payload,
            module_run_id=run.module_run_id,
            parent_module_run_id=run.parent_module_run_id,
            idempotency_key=idempotency_key,
        )

    async def _set_active_depth(
        self,
        *,
        conversation_id: str,
        user_id: str,
        depth: int,
    ) -> None:
        conversation = await self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise LookupError("conversation not found")
        await self._repository.update_metadata(
            conversation_id=conversation_id,
            user_id=user_id,
            expected_version=conversation.version,
            active_module_depth=depth,
        )

    async def _control_response(
        self,
        *,
        conversation_id: str,
        user_id: str,
        events: list[ConversationEvent],
        response: str,
    ) -> ModuleControlResponse:
        conversation = await self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise LookupError("conversation not found")
        return ModuleControlResponse(
            conversation=conversation,
            active_module_stack=await self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            appended_events=events,
            response=response,
        )


def _module_run_id(proposal_id: str) -> str:
    return sha256(f"module-run:{proposal_id}".encode("utf-8")).hexdigest()[:32]
