"""SQLite-backed protocol store."""

from datetime import datetime, timezone
from uuid import uuid4

from app.db.config import database_settings
from app.db.engine import connect
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.db.session import initialize_database
from app.models_protocols import ProtocolRecord, ProtocolStatus, ProtocolType


class ProtocolStore:
    """Persist and update protocol records."""

    def __init__(self) -> None:
        if resolve_database_provider(database_settings().database_url) == DatabaseProvider.SQLITE:
            initialize_database()

    async def create(
        self,
        *,
        user_id: str,
        protocol_type: ProtocolType,
        session_id: str | None,
        harness_action: str,
        request_hash: str,
        expires_at: datetime,
        payload: dict[str, object],
    ) -> ProtocolRecord:
        """Create a pending protocol record."""
        now = datetime.now(timezone.utc)
        record = ProtocolRecord(
            protocol_id=str(uuid4()),
            user_id=user_id,
            protocol_type=protocol_type,
            status=ProtocolStatus.PENDING,
            session_id=session_id,
            harness_action=harness_action,
            request_hash=request_hash,
            payload=payload,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        return await self.save(record)

    async def save(self, record: ProtocolRecord) -> ProtocolRecord:
        """Persist one protocol record."""
        with connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO protocols
                (protocol_id, user_id, protocol_type, status, session_id, harness_action,
                request_hash, expires_at, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.protocol_id,
                    record.user_id,
                    record.protocol_type.value,
                    record.status.value,
                    record.session_id,
                    record.harness_action,
                    record.request_hash,
                    record.expires_at.isoformat() if record.expires_at else None,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    async def get_for_user(
        self,
        protocol_id: str,
        user_id: str,
    ) -> ProtocolRecord | None:
        """Return a protocol only if it belongs to the user."""
        with connect() as connection:
            row = connection.execute(
                "SELECT payload FROM protocols WHERE protocol_id = ? AND user_id = ?",
                (protocol_id, user_id),
            ).fetchone()
        return ProtocolRecord.model_validate_json(row["payload"]) if row else None

    async def set_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        status: ProtocolStatus,
    ) -> ProtocolRecord | None:
        """Update a protocol status if it belongs to the user."""
        record = await self.get_for_user(protocol_id, user_id)
        if record is None:
            return None
        now = datetime.now(timezone.utc)
        timestamp_updates: dict[str, datetime] = {}
        if status == ProtocolStatus.APPROVED:
            timestamp_updates["approved_at"] = now
        elif status == ProtocolStatus.REJECTED:
            timestamp_updates["rejected_at"] = now
        elif status == ProtocolStatus.CONSUMED:
            timestamp_updates["consumed_at"] = now
        updated = record.model_copy(
            update={
                "status": status,
                "updated_at": now,
                **timestamp_updates,
            }
        )
        return await self.save(updated)

    async def transition_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        expected_status: ProtocolStatus,
        next_status: ProtocolStatus,
    ) -> ProtocolRecord | None:
        """Atomically transition status when the current status matches."""
        record = await self.get_for_user(protocol_id, user_id)
        if record is None or record.status != expected_status:
            return None
        now = datetime.now(timezone.utc)
        timestamp_updates: dict[str, datetime] = {}
        if next_status == ProtocolStatus.APPROVED:
            timestamp_updates["approved_at"] = now
        elif next_status == ProtocolStatus.REJECTED:
            timestamp_updates["rejected_at"] = now
        elif next_status == ProtocolStatus.CONSUMED:
            timestamp_updates["consumed_at"] = now
        updated = record.model_copy(
            update={
                "status": next_status,
                "updated_at": now,
                **timestamp_updates,
            }
        )
        with connect() as connection:
            cursor = connection.execute(
                """UPDATE protocols
                SET status = ?, payload = ?, updated_at = ?
                WHERE protocol_id = ? AND user_id = ? AND status = ?""",
                (
                    next_status.value,
                    updated.model_dump_json(),
                    updated.updated_at.isoformat(),
                    protocol_id,
                    user_id,
                    expected_status.value,
                ),
            )
        return updated if cursor.rowcount == 1 else None

    async def expire_pending_before(self, cutoff: datetime) -> int:
        """Expire pending protocols whose expiration timestamp has passed."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM protocols
                WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?""",
                (ProtocolStatus.PENDING.value, cutoff.isoformat()),
            ).fetchall()
        expired_count = 0
        for row in rows:
            record = ProtocolRecord.model_validate_json(row["payload"])
            if await self.transition_status(
                protocol_id=record.protocol_id,
                user_id=record.user_id,
                expected_status=ProtocolStatus.PENDING,
                next_status=ProtocolStatus.EXPIRED,
            ) is not None:
                expired_count += 1
        return expired_count


protocol_store = ProtocolStore()
