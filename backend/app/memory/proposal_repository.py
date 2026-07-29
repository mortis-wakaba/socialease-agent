"""Repository contract for confirmation-gated Memory Proposals."""

from datetime import datetime
from typing import Protocol

from app.models_long_term_memory import (
    MemoryProposalStatus,
    PendingMemoryProposalRecord,
)


class MemoryProposalRepository(Protocol):
    """Persistence contract for safe proposals awaiting confirmation."""

    async def save_pending(
        self,
        record: PendingMemoryProposalRecord,
    ) -> PendingMemoryProposalRecord: ...

    async def get_for_user(
        self,
        proposal_id: str,
        user_id: str,
    ) -> PendingMemoryProposalRecord | None: ...

    async def get_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> PendingMemoryProposalRecord | None: ...

    async def list_pending(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PendingMemoryProposalRecord]: ...

    async def consume_pending(
        self,
        *,
        user_id: str,
        proposal_id: str,
        expected_version: int,
        target_status: MemoryProposalStatus,
        reason_code: str,
        changed_at: datetime,
    ) -> None: ...

    async def record_rejection(
        self,
        *,
        user_id: str,
        proposal_id: str,
        reason_code: str,
        created_at: datetime,
    ) -> None: ...
