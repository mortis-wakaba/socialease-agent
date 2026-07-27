"""State and nesting policy for user-confirmed conversation modules."""

from collections.abc import Sequence

from app.models_conversation import (
    MAX_MODULE_DEPTH,
    ConversationStatus,
    ModuleProposalStatus,
    ModuleRun,
    ModuleRunStatus,
    ModuleType,
)


class ConversationStateError(ValueError):
    """Raised when a conversation domain transition violates policy."""


class ModuleStackPolicy:
    """Validate lifecycle transitions without mutating persisted state."""

    _conversation_transitions = {
        ConversationStatus.ACTIVE: {
            ConversationStatus.ARCHIVED,
            ConversationStatus.DELETED,
        },
        ConversationStatus.ARCHIVED: {
            ConversationStatus.ACTIVE,
            ConversationStatus.DELETED,
        },
        ConversationStatus.DELETED: set(),
    }
    _proposal_transitions = {
        ModuleProposalStatus.PENDING: {
            ModuleProposalStatus.ACCEPTED,
            ModuleProposalStatus.REJECTED,
            ModuleProposalStatus.EXPIRED,
        },
        ModuleProposalStatus.ACCEPTED: set(),
        ModuleProposalStatus.REJECTED: set(),
        ModuleProposalStatus.EXPIRED: set(),
    }
    _run_transitions = {
        ModuleRunStatus.ACTIVE: {
            ModuleRunStatus.SUSPENDED,
            ModuleRunStatus.COMPLETED,
            ModuleRunStatus.TERMINATED,
        },
        ModuleRunStatus.SUSPENDED: {
            ModuleRunStatus.ACTIVE,
            ModuleRunStatus.TERMINATED,
        },
        ModuleRunStatus.COMPLETED: set(),
        ModuleRunStatus.TERMINATED: set(),
    }
    _allowed_children = {
        ModuleType.ROLEPLAY: {ModuleType.EXPOSURE, ModuleType.WORKSHEET},
        ModuleType.WORKSHEET: {ModuleType.ROLEPLAY},
        ModuleType.EXPOSURE: {ModuleType.ROLEPLAY},
        ModuleType.RESOURCE: set(),
    }

    @classmethod
    def validate_conversation_transition(
        cls,
        current: ConversationStatus,
        target: ConversationStatus,
    ) -> None:
        """Validate a requested conversation lifecycle transition."""
        cls._validate_transition(
            current,
            target,
            cls._conversation_transitions,
            "conversation",
        )

    @classmethod
    def validate_proposal_transition(
        cls,
        current: ModuleProposalStatus,
        target: ModuleProposalStatus,
    ) -> None:
        """Validate a proposal's one-time decision transition."""
        cls._validate_transition(
            current,
            target,
            cls._proposal_transitions,
            "module proposal",
        )

    @classmethod
    def validate_run_transition(
        cls,
        current: ModuleRunStatus,
        target: ModuleRunStatus,
    ) -> None:
        """Validate a module frame lifecycle transition."""
        cls._validate_transition(current, target, cls._run_transitions, "module run")

    @classmethod
    def validate_push(
        cls,
        stack: Sequence[ModuleRun],
        child_type: ModuleType,
    ) -> None:
        """Validate depth, shape, and nesting before a confirmed push."""
        if len(stack) >= MAX_MODULE_DEPTH:
            raise ConversationStateError("maximum module depth reached")
        if not stack:
            return

        run_ids = [run.module_run_id for run in stack]
        if len(run_ids) != len(set(run_ids)):
            raise ConversationStateError("module stack contains a cycle")

        for index, run in enumerate(stack):
            if run.depth != index + 1:
                raise ConversationStateError("module stack depth is inconsistent")
            expected_parent = None if index == 0 else stack[index - 1].module_run_id
            if run.parent_module_run_id != expected_parent:
                raise ConversationStateError("module stack parent is inconsistent")
            expected_status = (
                ModuleRunStatus.ACTIVE
                if index == len(stack) - 1
                else ModuleRunStatus.SUSPENDED
            )
            if run.status is not expected_status:
                raise ConversationStateError("module stack status is inconsistent")

        parent = stack[-1].module_type
        if child_type not in cls._allowed_children[parent]:
            raise ConversationStateError(
                f"{parent.value} cannot contain {child_type.value}"
            )

    @staticmethod
    def safety_preempts_modules(*, crisis: bool, has_active_module: bool) -> bool:
        """Return whether routing must bypass the active module stack."""
        return crisis

    @staticmethod
    def _validate_transition(
        current: object,
        target: object,
        transitions: dict[object, set[object]],
        label: str,
    ) -> None:
        if target not in transitions[current]:
            raise ConversationStateError(
                f"invalid {label} transition: {current} -> {target}"
            )
