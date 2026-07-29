"""PostgreSQL adapter for user-owned memory export and erasure."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine
from app.memory.privacy_repository import UserDataDeleteScope
from app.memory.settings_payload import load_user_memory_settings_payload


AGENT_MEMORY_TABLES = (
    "user_memory_settings",
    "episodic_memories",
    "thread_checkpoints",
    "memory_events",
    "memory_proposals",
)
AGENT_MEMORY_DELETE_ORDER = (
    "memory_events",
    "memory_proposals",
    "thread_checkpoints",
    "episodic_memories",
    "user_memory_settings",
)
ACCOUNT_DATA_DELETE_ORDER = (
    *AGENT_MEMORY_DELETE_ORDER[:-1],
    "runs",
    "conversation_events",
    "conversation_module_proposals",
    "conversation_context_summaries",
    "conversation_module_runs",
    "conversations",
    "conversation_deletion_receipts",
    "roleplay_sessions",
    "worksheets",
    "exposure_attempts",
    "exposure_plans",
    "protocols",
    "intervention_plans",
    "session_reviews",
    "user_memory_settings",
)


class PostgresMemoryPrivacyRepository:
    """Keep table inventories and SQL inside the PostgreSQL boundary."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def export_agent_memory(
        self,
        *,
        user_id: str,
    ) -> dict[str, list[dict[str, object]]]:
        """Export only owner-scoped Agent Memory records."""
        records: dict[str, list[dict[str, object]]] = {}
        async with self.engine.connect() as connection:
            for table in AGENT_MEMORY_TABLES:
                rows = (
                    await connection.execute(
                        text(f"SELECT * FROM {table} WHERE user_id = :user_id"),
                        {"user_id": user_id},
                    )
                ).mappings().all()
                records[table] = [
                    _sanitize_memory_settings_export_row(_json_safe_row(dict(row)))
                    if table == "user_memory_settings"
                    else _json_safe_row(dict(row))
                    for row in rows
                ]
        return records

    async def delete_user_data(
        self,
        *,
        user_id: str,
        scope: UserDataDeleteScope,
    ) -> dict[str, int]:
        """Delete an explicit owner inventory in one transaction."""
        tables = (
            AGENT_MEMORY_DELETE_ORDER
            if scope is UserDataDeleteScope.AGENT_MEMORY
            else ACCOUNT_DATA_DELETE_ORDER
        )
        deleted_counts: dict[str, int] = {}
        async with self.engine.begin() as connection:
            for table in tables:
                result = await connection.execute(
                    text(f"DELETE FROM {table} WHERE user_id = :user_id"),
                    {"user_id": user_id},
                )
                deleted_counts[table] = result.rowcount or 0
        return deleted_counts


def _json_safe_row(row: dict[str, object]) -> dict[str, object]:
    """Convert driver values into JSON-compatible export values."""
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }


def _sanitize_memory_settings_export_row(
    row: dict[str, object],
) -> dict[str, object]:
    """Replace stored settings payload with the validated public representation."""
    payload = row.get("payload")
    settings = load_user_memory_settings_payload(
        payload if isinstance(payload, (str, dict)) or payload is None else None
    )
    row["payload"] = settings.model_dump(mode="json")
    return row
