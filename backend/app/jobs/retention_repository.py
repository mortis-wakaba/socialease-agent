"""Repository contract for retention-only physical deletion."""

from datetime import datetime
from typing import Protocol


class RetentionRepository(Protocol):
    """Delete expired durable records without exposing SQL to services."""

    async def delete_trace_records_before(self, cutoff: datetime) -> int: ...

    async def delete_terminal_protocols_before(self, cutoff: datetime) -> int: ...

    async def delete_terminal_intervention_plans_before(
        self,
        cutoff: datetime,
    ) -> int: ...
