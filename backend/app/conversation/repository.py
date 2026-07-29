"""Database-independent repository contract for unified conversations."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
import json
from typing import AsyncContextManager, Protocol

from app.models_conversation import (
    HISTORY_NOTICE_VERSION,
    Conversation,
    ConversationEvent,
    ConversationEventPage,
    ConversationEventPayload,
    ConversationEventRole,
    ConversationEventType,
    ConversationPage,
    ConversationStatus,
    ModuleProposal,
    ModuleProposalStatus,
    ModuleRun,
    ModuleRunStatus,
    ModuleType,
)
from app.models_conversation_context import ConversationCompactSummary


class ConversationConcurrencyError(RuntimeError):
    """Raised when an optimistic version or expected state no longer matches."""


class ConversationIdempotencyError(RuntimeError):
    """Raised when an idempotency key is reused for different content."""


@dataclass(frozen=True)
class ConversationCommandClaim:
    """Result of atomically claiming one conversation write command."""

    acquired: bool
    completed_result: str | None = None


@dataclass(frozen=True)
class ModuleStartJob:
    """Lease-owned module startup reconciliation job."""

    module_run_id: str
    conversation_id: str
    user_id: str
    proposal_id: str
    attempt_count: int
    max_attempts: int
    lease_owner: str


class ConversationRepository(Protocol):
    """Persistence contract for owner-scoped conversation timelines."""

    async def create(
        self,
        *,
        user_id: str,
        title: str,
        history_notice_version: str = HISTORY_NOTICE_VERSION,
    ) -> Conversation: ...

    async def get_for_user(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Conversation | None: ...

    async def list_for_user(
        self,
        user_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ConversationPage: ...

    async def update_metadata(
        self,
        *,
        conversation_id: str,
        user_id: str,
        expected_version: int,
        title: str | None = None,
        status: ConversationStatus | None = None,
        active_module_depth: int | None = None,
    ) -> Conversation: ...

    async def append_event(
        self,
        *,
        conversation_id: str,
        user_id: str,
        event_type: ConversationEventType,
        role: ConversationEventRole,
        content: str,
        idempotency_key: str,
        structured_payload: ConversationEventPayload | None = None,
        module_run_id: str | None = None,
        parent_module_run_id: str | None = None,
    ) -> ConversationEvent: ...

    async def list_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ConversationEventPage: ...

    async def list_recent_events(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int = 64,
    ) -> list[ConversationEvent]: ...

    async def get_event_by_idempotency(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ConversationEvent | None: ...

    async def claim_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ConversationCommandClaim: ...

    async def complete_command(
        self,
        *,
        conversation_id: str,
        user_id: str,
        idempotency_key: str,
        result: str,
    ) -> None: ...

    async def get_compact_summary(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> ConversationCompactSummary | None: ...

    async def save_compact_summary(
        self,
        summary: ConversationCompactSummary,
        *,
        expected_version: int | None,
    ) -> ConversationCompactSummary: ...

    async def save_proposal(self, proposal: ModuleProposal) -> ModuleProposal: ...

    async def get_proposal_for_user(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleProposal | None: ...

    async def get_proposal_by_request(
        self,
        *,
        conversation_id: str,
        user_id: str,
        request_id: str,
    ) -> ModuleProposal | None: ...

    async def transition_proposal(
        self,
        *,
        proposal_id: str,
        conversation_id: str,
        user_id: str,
        expected_status: ModuleProposalStatus,
        target_status: ModuleProposalStatus,
    ) -> ModuleProposal | None: ...

    async def create_module_run(self, run: ModuleRun) -> ModuleRun: ...

    def module_start_transaction(self) -> AsyncContextManager[None]: ...

    async def begin_module_start(
        self,
        *,
        proposal: ModuleProposal,
        run: ModuleRun,
        parent: ModuleRun | None,
    ) -> ModuleRun: ...

    async def claim_module_start(self, *, module_run_id: str) -> bool: ...

    async def claim_due_module_starts(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[ModuleStartJob]: ...

    async def complete_module_start(self, *, module_run_id: str) -> None: ...

    async def retry_module_start(
        self,
        *,
        module_run_id: str,
        error_code: str,
    ) -> None: ...

    async def list_module_stack(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]: ...

    async def get_module_run_for_user(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
    ) -> ModuleRun | None: ...

    async def list_all_module_runs(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleRun]: ...

    async def get_conversation_for_domain_session(
        self,
        *,
        user_id: str,
        module_type: ModuleType,
        domain_session_id: str,
    ) -> Conversation | None: ...

    async def list_proposals(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[ModuleProposal]: ...

    async def delete_for_user(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, int] | None: ...

    async def delete_all_for_user(self, *, user_id: str) -> dict[str, int]: ...

    async def update_module_domain_session(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_version: int,
        domain_session_id: str,
    ) -> ModuleRun: ...

    async def advance_module_run_version(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_version: int,
    ) -> ModuleRun: ...

    async def transition_module_run(
        self,
        *,
        module_run_id: str,
        conversation_id: str,
        user_id: str,
        expected_status: ModuleRunStatus,
        expected_version: int,
        target_status: ModuleRunStatus,
        ended_at: datetime | None,
    ) -> ModuleRun | None: ...


def _event_associated_data(
    event_id: str,
    conversation_id: str,
    user_id: str,
    sequence_no: int,
) -> bytes:
    return (
        f"{event_id}:{conversation_id}:{user_id}:{sequence_no}".encode("utf-8")
    )


def _validate_idempotent_event(
    *,
    existing: ConversationEvent,
    event_type: ConversationEventType,
    role: ConversationEventRole,
    content: str,
    structured_payload: ConversationEventPayload | None,
    module_run_id: str | None,
    parent_module_run_id: str | None,
) -> None:
    proposed_payload = (
        structured_payload.model_dump(mode="json") if structured_payload else None
    )
    existing_payload = (
        existing.structured_payload.model_dump(mode="json")
        if existing.structured_payload
        else None
    )
    if (
        existing.event_type != event_type
        or existing.role != role
        or existing.content != content
        or existing_payload != proposed_payload
        or existing.module_run_id != module_run_id
        or existing.parent_module_run_id != parent_module_run_id
    ):
        raise ConversationIdempotencyError(
            "idempotency key was already used for a different event"
        )


def _validated_limit(limit: int, *, maximum: int) -> int:
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


def _command_associated_data(
    conversation_id: str,
    user_id: str,
    idempotency_key: str,
) -> bytes:
    """Bind an encrypted command result to its owner and logical command."""
    return (
        f"conversation-command:{conversation_id}:{user_id}:{idempotency_key}"
    ).encode("utf-8")


def _encode_conversation_cursor(updated_at: str, conversation_id: str) -> str:
    raw = json.dumps([updated_at, conversation_id], separators=(",", ":"))
    return urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_conversation_cursor(cursor: str) -> tuple[str, str]:
    try:
        decoded = json.loads(urlsafe_b64decode(cursor.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid conversation cursor") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or not all(isinstance(value, str) for value in decoded)
    ):
        raise ValueError("invalid conversation cursor")
    return decoded[0], decoded[1]


def _encode_event_cursor(sequence_no: int) -> str:
    return urlsafe_b64encode(str(sequence_no).encode("ascii")).decode("ascii")


def _decode_event_cursor(cursor: str) -> int:
    try:
        sequence_no = int(
            urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        )
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid event cursor") from exc
    if sequence_no < 0:
        raise ValueError("invalid event cursor")
    return sequence_no
