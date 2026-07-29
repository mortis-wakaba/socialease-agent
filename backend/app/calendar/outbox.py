"""Transactional outbox for consented Calendar MCP side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Literal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine
from app.models_protocols import ProtocolRecord, ProtocolStatus


CalendarActionType = Literal["create", "update", "delete"]


@dataclass(frozen=True)
class CalendarActionJob:
    """One leased or completed Calendar outbox action."""

    job_id: str
    protocol_id: str | None
    user_id: str
    action_type: CalendarActionType
    request_hash: str
    idempotency_key: str
    payload: dict[str, object]
    status: str
    attempt_count: int
    max_attempts: int
    lease_owner: str | None = None
    result: dict[str, object] | None = None


@dataclass(frozen=True)
class OutboxHealth:
    """Non-sensitive queue health used by readiness and operations."""

    pending: int
    processing: int
    dead_letter: int
    oldest_pending_seconds: int


class CalendarActionOutbox:
    """Persist consent consumption and external work as one local transaction."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.database_url = database_url or database_settings().database_url
        self.engine = engine or shared_postgres_async_engine(self.database_url)

    async def enqueue(
        self,
        *,
        protocol_id: str | None,
        user_id: str,
        action_type: CalendarActionType,
        request_hash: str,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> CalendarActionJob | None:
        """Atomically consume exact consent and enqueue its external action."""
        return await self._enqueue_postgres(
            protocol_id=protocol_id,
            user_id=user_id,
            action_type=action_type,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    async def claim(
        self,
        *,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 60,
        job_id: str | None = None,
    ) -> list[CalendarActionJob]:
        """Lease due jobs with concurrent-claim protection."""
        return await self._claim_postgres(
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
            job_id=job_id,
        )

    async def complete(
        self,
        *,
        job_id: str,
        lease_owner: str,
        result: dict[str, object],
    ) -> bool:
        """Complete only the lease owned by the caller."""
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            updated = await connection.execute(
                text(
                    """UPDATE calendar_action_outbox
                    SET status = 'completed', result = CAST(:result AS jsonb),
                        completed_at = :now, updated_at = :now,
                        lease_owner = NULL, lease_expires_at = NULL,
                        last_error_code = NULL
                    WHERE job_id = :job_id AND status = 'processing'
                      AND lease_owner = :lease_owner"""
                ),
                {
                    "job_id": job_id,
                    "lease_owner": lease_owner,
                    "result": json.dumps(result),
                    "now": now,
                },
            )
        return updated.rowcount == 1

    async def retry(
        self,
        *,
        job_id: str,
        lease_owner: str,
        error_code: str,
    ) -> str | None:
        """Apply bounded exponential backoff or move a job to dead letter."""
        return await self._retry_postgres(job_id, lease_owner, error_code)

    async def get(self, job_id: str) -> CalendarActionJob | None:
        """Return one job by id."""
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """SELECT * FROM calendar_action_outbox
                        WHERE job_id = :job_id"""
                    ),
                    {"job_id": job_id},
                )
            ).mappings().first()
        return _postgres_job(row) if row else None

    async def health(self) -> OutboxHealth:
        """Return queue counts without payloads or user identifiers."""
        now = datetime.now(UTC)
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """SELECT
                        COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                        COUNT(*) FILTER (WHERE status = 'processing') AS processing,
                        COUNT(*) FILTER (WHERE status = 'dead_letter') AS dead_letter,
                        MIN(created_at) FILTER (
                            WHERE status IN ('pending', 'processing')
                        ) AS oldest
                        FROM calendar_action_outbox"""
                    )
                )
            ).mappings().one()
        oldest = row["oldest"]
        return OutboxHealth(
            pending=int(row["pending"]),
            processing=int(row["processing"]),
            dead_letter=int(row["dead_letter"]),
            oldest_pending_seconds=max(
                0, int((now - oldest).total_seconds())
            ) if oldest else 0,
        )

    async def _enqueue_postgres(self, **values: object) -> CalendarActionJob | None:
        assert self.engine is not None
        now = datetime.now(UTC)
        protocol_id = values["protocol_id"]
        async with self.engine.begin() as connection:
            if protocol_id is not None:
                existing = (
                    await connection.execute(
                        text(
                            """SELECT * FROM calendar_action_outbox
                            WHERE protocol_id = :protocol_id"""
                        ),
                        {"protocol_id": protocol_id},
                    )
                ).mappings().first()
                if existing is not None:
                    job = _postgres_job(existing)
                    return job if _same_request(job, values) else None
                row = (
                    await connection.execute(
                        text(
                            """SELECT payload FROM protocols
                            WHERE protocol_id = :protocol_id AND user_id = :user_id
                            FOR UPDATE"""
                        ),
                        {
                            "protocol_id": protocol_id,
                            "user_id": values["user_id"],
                        },
                    )
                ).mappings().first()
                if row is None:
                    return None
                existing = (
                    await connection.execute(
                        text(
                            """SELECT * FROM calendar_action_outbox
                            WHERE protocol_id = :protocol_id"""
                        ),
                        {"protocol_id": protocol_id},
                    )
                ).mappings().first()
                if existing is not None:
                    job = _postgres_job(existing)
                    return job if _same_request(job, values) else None
                protocol = ProtocolRecord.model_validate(row["payload"])
                if not _valid_protocol(protocol, values, now):
                    return None
                consumed = protocol.model_copy(
                    update={
                        "status": ProtocolStatus.CONSUMED,
                        "consumed_at": now,
                        "updated_at": now,
                    }
                )
                await connection.execute(
                    text(
                        """UPDATE protocols SET status = 'consumed',
                        payload = CAST(:payload AS jsonb), updated_at = :now
                        WHERE protocol_id = :protocol_id"""
                    ),
                    {
                        "payload": consumed.model_dump_json(),
                        "now": now,
                        "protocol_id": protocol_id,
                    },
                )
            else:
                existing = (
                    await connection.execute(
                        text(
                            """SELECT * FROM calendar_action_outbox
                            WHERE protocol_id IS NULL AND user_id = :user_id
                              AND action_type = :action_type
                              AND request_hash = :request_hash"""
                        ),
                        values,
                    )
                ).mappings().first()
                if existing is not None:
                    return _postgres_job(existing)
            job = _new_job(values, now)
            await connection.execute(
                text(
                    """INSERT INTO calendar_action_outbox
                    (job_id, protocol_id, user_id, action_type, request_hash,
                     idempotency_key, payload, status, attempt_count, max_attempts,
                     next_attempt_at, created_at, updated_at)
                    VALUES (:job_id, :protocol_id, :user_id, :action_type,
                    :request_hash, :idempotency_key, CAST(:payload AS jsonb),
                    'pending', 0, :max_attempts, :now, :now, :now)"""
                ),
                {
                    **job.__dict__,
                    "payload": json.dumps(job.payload),
                    "now": now,
                },
            )
        return job

    async def _claim_postgres(
        self, *, worker_id: str, limit: int, lease_seconds: int, job_id: str | None
    ) -> list[CalendarActionJob]:
        assert self.engine is not None
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            rows = (
                await connection.execute(
                    text(
                        """WITH due AS (
                        SELECT job_id FROM calendar_action_outbox
                        WHERE (
                            CAST(:job_id AS varchar) IS NULL
                            OR job_id = CAST(:job_id AS varchar)
                        )
                          AND ((status = 'pending' AND next_attempt_at <= :now)
                            OR (status = 'processing' AND lease_expires_at <= :now))
                        ORDER BY next_attempt_at, created_at
                        FOR UPDATE SKIP LOCKED LIMIT :limit
                        )
                        UPDATE calendar_action_outbox AS jobs
                        SET status = 'processing',
                            attempt_count = jobs.attempt_count + 1,
                            lease_owner = :worker_id,
                            lease_expires_at = :lease_expires_at,
                            updated_at = :now
                        FROM due WHERE jobs.job_id = due.job_id
                        RETURNING jobs.*"""
                    ),
                    {
                        "job_id": job_id,
                        "now": now,
                        "limit": max(1, min(limit, 100)),
                        "worker_id": worker_id,
                        "lease_expires_at": now
                        + timedelta(seconds=max(1, lease_seconds)),
                    },
                )
            ).mappings().all()
        return [_postgres_job(row) for row in rows]

    async def _retry_postgres(
        self, job_id: str, lease_owner: str, error_code: str
    ) -> str | None:
        assert self.engine is not None
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        """SELECT attempt_count, max_attempts
                        FROM calendar_action_outbox
                        WHERE job_id = :job_id AND status = 'processing'
                          AND lease_owner = :lease_owner FOR UPDATE"""
                    ),
                    {"job_id": job_id, "lease_owner": lease_owner},
                )
            ).mappings().first()
            if row is None:
                return None
            status, next_attempt = _retry_state(row, now)
            await connection.execute(
                text(
                    """UPDATE calendar_action_outbox SET status = :status,
                    next_attempt_at = :next_attempt, last_error_code = :error_code,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = :now
                    WHERE job_id = :job_id"""
                ),
                {
                    "status": status,
                    "next_attempt": next_attempt,
                    "error_code": error_code[:64],
                    "now": now,
                    "job_id": job_id,
                },
            )
        return status


def _valid_protocol(
    protocol: ProtocolRecord,
    values: dict[str, object],
    now: datetime,
) -> bool:
    return (
        protocol.status is ProtocolStatus.APPROVED
        and protocol.user_id == values["user_id"]
        and protocol.harness_action == f"{values['action_type']}_calendar_event"
        and protocol.request_hash == values["request_hash"]
        and (protocol.expires_at is None or protocol.expires_at > now)
    )


def _same_request(job: CalendarActionJob, values: dict[str, object]) -> bool:
    return (
        job.user_id == values["user_id"]
        and job.action_type == values["action_type"]
        and job.request_hash == values["request_hash"]
    )


def _new_job(values: dict[str, object], now: datetime) -> CalendarActionJob:
    return CalendarActionJob(
        job_id=str(uuid4()),
        protocol_id=values["protocol_id"] if isinstance(values["protocol_id"], str) else None,
        user_id=str(values["user_id"]),
        action_type=values["action_type"],  # type: ignore[arg-type]
        request_hash=str(values["request_hash"]),
        idempotency_key=str(values["idempotency_key"]),
        payload=values["payload"],  # type: ignore[arg-type]
        status="pending",
        attempt_count=0,
        max_attempts=8,
    )


def _retry_state(row: object, now: datetime) -> tuple[str, datetime]:
    attempt_count = int(row["attempt_count"])  # type: ignore[index]
    max_attempts = int(row["max_attempts"])  # type: ignore[index]
    status = "dead_letter" if attempt_count >= max_attempts else "pending"
    delay = min(300, 2 ** max(0, attempt_count - 1))
    return status, now + timedelta(seconds=delay)


def _postgres_job(row: object) -> CalendarActionJob:
    return CalendarActionJob(
        job_id=row["job_id"],  # type: ignore[index]
        protocol_id=row["protocol_id"],  # type: ignore[index]
        user_id=row["user_id"],  # type: ignore[index]
        action_type=row["action_type"],  # type: ignore[index]
        request_hash=row["request_hash"],  # type: ignore[index]
        idempotency_key=row["idempotency_key"],  # type: ignore[index]
        payload=row["payload"],  # type: ignore[index]
        status=row["status"],  # type: ignore[index]
        attempt_count=int(row["attempt_count"]),  # type: ignore[index]
        max_attempts=int(row["max_attempts"]),  # type: ignore[index]
        lease_owner=row["lease_owner"],  # type: ignore[index]
        result=row["result"],  # type: ignore[index]
    )
