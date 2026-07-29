"""Consent Protocol repository contract and non-persistent test fake."""

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from app.models_protocols import ProtocolRecord, ProtocolStatus, ProtocolType


class ProtocolRepository(Protocol):
    """Persistence contract for consent protocol records."""

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
    ) -> ProtocolRecord: ...

    async def save(self, record: ProtocolRecord) -> ProtocolRecord: ...

    async def get_for_user(
        self,
        protocol_id: str,
        user_id: str,
    ) -> ProtocolRecord | None: ...

    async def set_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        status: ProtocolStatus,
    ) -> ProtocolRecord | None: ...

    async def transition_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        expected_status: ProtocolStatus,
        next_status: ProtocolStatus,
    ) -> ProtocolRecord | None: ...

    async def expire_pending_before(self, cutoff: datetime) -> int: ...


class InMemoryProtocolRepository:
    """Non-persistent Protocol fake for isolated unit and eval tests."""

    def __init__(self) -> None:
        self.records: dict[str, ProtocolRecord] = {}

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
        self.records[record.protocol_id] = record
        return record

    async def save(self, record: ProtocolRecord) -> ProtocolRecord:
        self.records[record.protocol_id] = record
        return record

    async def get_for_user(
        self,
        protocol_id: str,
        user_id: str,
    ) -> ProtocolRecord | None:
        record = self.records.get(protocol_id)
        return record if record is not None and record.user_id == user_id else None

    async def set_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        status: ProtocolStatus,
    ) -> ProtocolRecord | None:
        record = await self.get_for_user(protocol_id, user_id)
        if record is None:
            return None
        return await self.save(_with_protocol_status(record, status))

    async def transition_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        expected_status: ProtocolStatus,
        next_status: ProtocolStatus,
    ) -> ProtocolRecord | None:
        record = await self.get_for_user(protocol_id, user_id)
        if record is None or record.status is not expected_status:
            return None
        return await self.save(_with_protocol_status(record, next_status))

    async def expire_pending_before(self, cutoff: datetime) -> int:
        expired = 0
        for record in list(self.records.values()):
            if (
                record.status is ProtocolStatus.PENDING
                and record.expires_at is not None
                and record.expires_at <= cutoff
            ):
                await self.save(
                    _with_protocol_status(record, ProtocolStatus.EXPIRED)
                )
                expired += 1
        return expired


def _with_protocol_status(
    record: ProtocolRecord,
    status: ProtocolStatus,
) -> ProtocolRecord:
    now = datetime.now(timezone.utc)
    timestamps: dict[str, datetime] = {}
    if status is ProtocolStatus.APPROVED:
        timestamps["approved_at"] = now
    elif status is ProtocolStatus.REJECTED:
        timestamps["rejected_at"] = now
    elif status is ProtocolStatus.CONSUMED:
        timestamps["consumed_at"] = now
    return record.model_copy(
        update={"status": status, "updated_at": now, **timestamps}
    )
