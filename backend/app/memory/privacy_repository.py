"""Repository contract for user-owned memory export and erasure."""

from enum import StrEnum
from typing import Protocol


class UserDataDeleteScope(StrEnum):
    """Explicit deletion inventories owned by the persistence adapter."""

    AGENT_MEMORY = "agent_memory"
    ACCOUNT = "account"


class MemoryPrivacyRepository(Protocol):
    """Persistence contract for export and dependency-safe erasure."""

    async def export_agent_memory(
        self,
        *,
        user_id: str,
    ) -> dict[str, list[dict[str, object]]]: ...

    async def delete_user_data(
        self,
        *,
        user_id: str,
        scope: UserDataDeleteScope,
    ) -> dict[str, int]: ...
