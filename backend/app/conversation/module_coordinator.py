"""Coordinate confirmed module frames and domain-service adapters."""

from datetime import UTC, datetime
from hashlib import sha256

from app.conversation.adapters import ModuleAdapter, ModuleAdapterResult
from app.conversation.module_policy import ModuleStackPolicy
from app.conversation.repository import ConversationRepository
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
)
from app.models_conversation_api import ModuleControlResponse


class ModuleCoordinator:
    """Own stack transitions while domain services own module behavior."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        adapters: dict[ModuleType, ModuleAdapter],
    ) -> None:
        self._repository = repository
        self._adapters = adapters

    async def accept(self, proposal: ModuleProposal) -> ModuleControlResponse:
        """Consume one proposal and push its confirmed module frame."""
        module_run_id = _module_run_id(proposal.proposal_id)
        existing = self._repository.get_module_run_for_user(
            module_run_id=module_run_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
        )
        if existing is not None:
            return self._control_response(
                conversation_id=proposal.conversation_id,
                user_id=proposal.user_id,
                events=[],
                response="这个模块已经进入当前会话。",
            )

        stack = self._repository.list_module_stack(
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
        )
        ModuleStackPolicy.validate_push(stack, proposal.proposed_module)
        accepted = self._repository.transition_proposal(
            proposal_id=proposal.proposal_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
            expected_status=ModuleProposalStatus.PENDING,
            target_status=ModuleProposalStatus.ACCEPTED,
        )
        if accepted is None:
            raise LookupError("module proposal not found")

        now = datetime.now(UTC)
        run = ModuleRun(
            module_run_id=module_run_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
            module_type=proposal.proposed_module,
            parent_module_run_id=(
                stack[-1].module_run_id if stack else None
            ),
            depth=len(stack) + 1,
            module_parameters=proposal.bounded_parameters,
            started_at=now,
        )
        adapter = self._adapter(run.module_type)
        result = await adapter.start(run)
        run = run.model_copy(
            update={"domain_session_id": result.domain_session_id}
        )
        events: list[ConversationEvent] = []
        if stack:
            parent = stack[-1]
            await self._adapter(parent.module_type).suspend(parent)
            suspended = self._repository.transition_module_run(
                module_run_id=parent.module_run_id,
                conversation_id=parent.conversation_id,
                user_id=parent.user_id,
                expected_status=ModuleRunStatus.ACTIVE,
                expected_version=parent.version,
                target_status=ModuleRunStatus.SUSPENDED,
                ended_at=None,
            )
            if suspended is None:
                raise LookupError("parent module run not found")
            events.append(
                self._append_lifecycle_event(
                    run=suspended,
                    event_type=ConversationEventType.MODULE_SUSPENDED,
                    content="父模块已暂停，等待子模块结束后恢复。",
                    idempotency_key=f"module-suspended:{proposal.proposal_id}",
                )
            )
        self._repository.create_module_run(run)
        events.append(
            self._append_lifecycle_event(
                run=run,
                event_type=ConversationEventType.MODULE_STARTED,
                content=f"已进入 {run.module_type.value} 模块。",
                idempotency_key=f"module-started:{proposal.proposal_id}",
            )
        )
        events.append(
            self._append_result_event(
                run=run,
                result=result,
                idempotency_key=f"module-opening:{proposal.proposal_id}",
            )
        )
        self._set_active_depth(
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            depth=run.depth,
        )
        return self._control_response(
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
    ) -> tuple[ModuleAdapterResult, ConversationEvent]:
        """Send one message to the active top module frame."""
        stack = self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if not stack:
            raise LookupError("active module not found")
        run = stack[-1]
        result = await self._adapter(run.module_type).handle_message(
            run,
            message,
        )
        if (
            result.domain_session_id is not None
            and result.domain_session_id != run.domain_session_id
        ):
            run = self._repository.update_module_domain_session(
                module_run_id=run.module_run_id,
                conversation_id=run.conversation_id,
                user_id=run.user_id,
                expected_version=run.version,
                domain_session_id=result.domain_session_id,
            )
        event = self._append_result_event(
            run=run,
            result=result,
            idempotency_key=f"module-response:{idempotency_key}",
        )
        return result, event

    async def terminate_current(
        self,
        *,
        conversation_id: str,
        user_id: str,
        module_run_id: str,
    ) -> ModuleControlResponse:
        """Terminate only the top frame and resume its parent."""
        stack = self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if not stack or stack[-1].module_run_id != module_run_id:
            existing = self._repository.get_module_run_for_user(
                module_run_id=module_run_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if existing is not None and existing.status == ModuleRunStatus.TERMINATED:
                return self._control_response(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    events=[],
                    response="当前模块已经结束。",
                )
            raise LookupError("active top module not found")
        top = stack[-1]
        await self._adapter(top.module_type).terminate(top)
        ended_at = datetime.now(UTC)
        terminated = self._repository.transition_module_run(
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
        events = [
            self._append_lifecycle_event(
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
            resumed = self._repository.transition_module_run(
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
            events.append(
                self._append_lifecycle_event(
                    run=resumed,
                    event_type=ConversationEventType.MODULE_RESUMED,
                    content="已恢复父模块。",
                    idempotency_key=f"module-resumed:{top.module_run_id}",
                )
            )
            response = "已结束当前模块，并恢复上一层练习。"
        self._set_active_depth(
            conversation_id=conversation_id,
            user_id=user_id,
            depth=max(0, len(stack) - 1),
        )
        return self._control_response(
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
        stack = self._repository.list_module_stack(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        events: list[ConversationEvent] = []
        for run in reversed(stack):
            await self._adapter(run.module_type).terminate(run)
            terminated = self._repository.transition_module_run(
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
            events.append(
                self._append_lifecycle_event(
                    run=terminated,
                    event_type=ConversationEventType.MODULE_TERMINATED,
                    content="用户已结束全部模块。",
                    idempotency_key=(
                        f"module-terminated-all:{run.module_run_id}"
                    ),
                )
            )
        self._set_active_depth(
            conversation_id=conversation_id,
            user_id=user_id,
            depth=0,
        )
        return self._control_response(
            conversation_id=conversation_id,
            user_id=user_id,
            events=events,
            response="已结束全部模块，返回普通对话。",
        )

    async def delete_runtime_contexts(self, runs: list[ModuleRun]) -> None:
        """Delete short-lived adapter state before durable conversation deletion."""
        for run in runs:
            await self._adapter(run.module_type).delete_runtime_context(run)

    def _adapter(self, module_type: ModuleType) -> ModuleAdapter:
        adapter = self._adapters.get(module_type)
        if adapter is None:
            raise ValueError(f"module adapter is unavailable: {module_type.value}")
        return adapter

    def _append_lifecycle_event(
        self,
        *,
        run: ModuleRun,
        event_type: ConversationEventType,
        content: str,
        idempotency_key: str,
    ) -> ConversationEvent:
        return self._repository.append_event(
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

    def _append_result_event(
        self,
        *,
        run: ModuleRun,
        result: ModuleAdapterResult,
        idempotency_key: str,
    ) -> ConversationEvent:
        return self._repository.append_event(
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

    def _set_active_depth(
        self,
        *,
        conversation_id: str,
        user_id: str,
        depth: int,
    ) -> None:
        conversation = self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise LookupError("conversation not found")
        self._repository.update_metadata(
            conversation_id=conversation_id,
            user_id=user_id,
            expected_version=conversation.version,
            active_module_depth=depth,
        )

    def _control_response(
        self,
        *,
        conversation_id: str,
        user_id: str,
        events: list[ConversationEvent],
        response: str,
    ) -> ModuleControlResponse:
        conversation = self._repository.get_for_user(
            conversation_id,
            user_id,
        )
        if conversation is None:
            raise LookupError("conversation not found")
        return ModuleControlResponse(
            conversation=conversation,
            active_module_stack=self._repository.list_module_stack(
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            appended_events=events,
            response=response,
        )


def _module_run_id(proposal_id: str) -> str:
    return sha256(f"module-run:{proposal_id}".encode("utf-8")).hexdigest()[:32]
