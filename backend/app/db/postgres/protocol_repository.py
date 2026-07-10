"""PostgreSQL protocol repository implementation."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models_intervention import InterventionPlan
from app.models_protocols import ProtocolRecord, ProtocolStatus, ProtocolType


class PostgresProtocolRepository:
    """PostgreSQL-backed protocol repository with expected-state transitions."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def create(
        self,
        *,
        user_id: str,
        protocol_type: ProtocolType,
        session_id: str | None,
        harness_action: str,
        request_hash: str,
        expires_at,
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
        return self.save(record)

    def get_for_user(self, protocol_id: str, user_id: str) -> ProtocolRecord | None:
        """Return a protocol only if it belongs to the user."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT payload FROM protocols
                    WHERE protocol_id = :protocol_id AND user_id = :user_id"""
                ),
                {"protocol_id": protocol_id, "user_id": user_id},
            ).mappings().first()
        return ProtocolRecord.model_validate(row["payload"]) if row else None

    def save(self, record: ProtocolRecord) -> ProtocolRecord:
        """Persist one protocol record."""
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO protocols
                    (protocol_id, user_id, protocol_type, status, session_id, harness_action,
                    request_hash, expires_at, payload, created_at, updated_at)
                    VALUES
                    (:protocol_id, :user_id, :protocol_type, :status, :session_id, :harness_action,
                    :request_hash, :expires_at, CAST(:payload AS jsonb), :created_at, :updated_at)
                    ON CONFLICT (protocol_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        protocol_type = EXCLUDED.protocol_type,
                        status = EXCLUDED.status,
                        session_id = EXCLUDED.session_id,
                        harness_action = EXCLUDED.harness_action,
                        request_hash = EXCLUDED.request_hash,
                        expires_at = EXCLUDED.expires_at,
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at"""
                ),
                _protocol_params(record),
            )
        return record

    def set_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        status: ProtocolStatus,
    ) -> ProtocolRecord | None:
        """Update status for a user-owned protocol."""
        record = self.get_for_user(protocol_id, user_id)
        if record is None:
            return None
        now = datetime.now(timezone.utc)
        updated = _with_status(record, status, now)
        return self.save(updated)

    def transition_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        expected_status: ProtocolStatus,
        next_status: ProtocolStatus,
    ) -> ProtocolRecord | None:
        """Atomically transition status when the current status matches."""
        record = self.get_for_user(protocol_id, user_id)
        if record is None or record.status != expected_status:
            return None
        now = datetime.now(timezone.utc)
        updated = _with_status(record, next_status, now)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """UPDATE protocols
                    SET status = :next_status,
                        payload = CAST(:payload AS jsonb),
                        updated_at = :updated_at
                    WHERE protocol_id = :protocol_id
                      AND user_id = :user_id
                      AND status = :expected_status"""
                ),
                {
                    "next_status": next_status.value,
                    "payload": updated.model_dump_json(),
                    "updated_at": updated.updated_at,
                    "protocol_id": protocol_id,
                    "user_id": user_id,
                    "expected_status": expected_status.value,
                },
            )
        return updated if result.rowcount == 1 else None

    def expire_pending_before(self, cutoff) -> int:
        """Expire pending protocols whose expiration timestamp has passed."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM protocols
                    WHERE status = :status
                      AND expires_at IS NOT NULL
                      AND expires_at <= :cutoff"""
                ),
                {"status": ProtocolStatus.PENDING.value, "cutoff": cutoff},
            ).mappings().all()
        expired_count = 0
        for row in rows:
            record = ProtocolRecord.model_validate(row["payload"])
            if self.transition_status(
                protocol_id=record.protocol_id,
                user_id=record.user_id,
                expected_status=ProtocolStatus.PENDING,
                next_status=ProtocolStatus.EXPIRED,
            ) is not None:
                expired_count += 1
        return expired_count

    def respond_with_linked_intervention_plan(
        self,
        *,
        protocol_id: str,
        user_id: str,
        approved: bool,
        now: datetime,
    ) -> ProtocolRecord | None:
        """Respond to a pending protocol and update its linked plan in one transaction."""
        with self.engine.begin() as connection:
            record = _locked_protocol(connection, protocol_id=protocol_id, user_id=user_id)
            if record is None:
                return None
            if record.expires_at is not None and record.expires_at <= now:
                updated = _with_status(record, ProtocolStatus.EXPIRED, now)
                _update_protocol(connection, updated)
                return updated
            if record.status != ProtocolStatus.PENDING:
                return record
            status = ProtocolStatus.APPROVED if approved else ProtocolStatus.REJECTED
            updated = _with_status(record, status, now)
            _update_protocol(connection, updated)
            plan_id = updated.payload.get("intervention_plan_id")
            if isinstance(plan_id, str):
                _sync_plan_after_protocol_response(
                    connection=connection,
                    user_id=user_id,
                    plan_id=plan_id,
                    protocol_status=status,
                    updated_at=now,
                )
            return updated

    def consume_with_linked_intervention_plan(
        self,
        *,
        protocol_id: str,
        user_id: str,
        harness_action: str,
        request_hash: str,
        session_id: str | None,
        now: datetime,
        result_session_id: str | None = None,
        result_summary: str | None = None,
    ) -> ProtocolRecord | None:
        """Consume an approved protocol and optionally complete its linked plan atomically."""
        with self.engine.begin() as connection:
            record = _locked_protocol(connection, protocol_id=protocol_id, user_id=user_id)
            if record is None:
                return None
            if record.expires_at is not None and record.expires_at <= now:
                _update_protocol(connection, _with_status(record, ProtocolStatus.EXPIRED, now))
                return None
            if record.status != ProtocolStatus.APPROVED:
                return None
            if record.harness_action != harness_action:
                return None
            if record.request_hash != request_hash:
                return None
            if record.session_id is not None and record.session_id != session_id:
                return None
            updated = _with_status(record, ProtocolStatus.CONSUMED, now)
            _update_protocol(connection, updated)
            plan_id = updated.payload.get("intervention_plan_id")
            if isinstance(plan_id, str) and result_summary is not None:
                _complete_linked_action_step(
                    connection=connection,
                    user_id=user_id,
                    plan_id=plan_id,
                    result_session_id=result_session_id,
                    result_summary=result_summary,
                    updated_at=now,
                )
            return updated


def _with_status(
    record: ProtocolRecord,
    status: ProtocolStatus,
    updated_at: datetime,
) -> ProtocolRecord:
    """Return a copy with status timestamps updated."""
    timestamp_updates: dict[str, datetime] = {}
    if status == ProtocolStatus.APPROVED:
        timestamp_updates["approved_at"] = updated_at
    elif status == ProtocolStatus.REJECTED:
        timestamp_updates["rejected_at"] = updated_at
    elif status == ProtocolStatus.CONSUMED:
        timestamp_updates["consumed_at"] = updated_at
    return record.model_copy(
        update={
            "status": status,
            "updated_at": updated_at,
            **timestamp_updates,
        }
    )


def _protocol_params(record: ProtocolRecord) -> dict[str, object]:
    """Return SQL parameters for a protocol record."""
    return {
        "protocol_id": record.protocol_id,
        "user_id": record.user_id,
        "protocol_type": record.protocol_type.value,
        "status": record.status.value,
        "session_id": record.session_id,
        "harness_action": record.harness_action,
        "request_hash": record.request_hash,
        "expires_at": record.expires_at,
        "payload": record.model_dump_json(),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _locked_protocol(
    connection: Connection,
    *,
    protocol_id: str,
    user_id: str,
) -> ProtocolRecord | None:
    """Return one protocol row locked for a transaction."""
    row = connection.execute(
        text(
            """SELECT payload FROM protocols
            WHERE protocol_id = :protocol_id AND user_id = :user_id
            FOR UPDATE"""
        ),
        {"protocol_id": protocol_id, "user_id": user_id},
    ).mappings().first()
    return ProtocolRecord.model_validate(row["payload"]) if row else None


def _update_protocol(connection: Connection, record: ProtocolRecord) -> None:
    """Persist an updated protocol inside an existing transaction."""
    connection.execute(
        text(
            """UPDATE protocols
            SET status = :status,
                payload = CAST(:payload AS jsonb),
                updated_at = :updated_at
            WHERE protocol_id = :protocol_id AND user_id = :user_id"""
        ),
        {
            "status": record.status.value,
            "payload": record.model_dump_json(),
            "updated_at": record.updated_at,
            "protocol_id": record.protocol_id,
            "user_id": record.user_id,
        },
    )


def _locked_intervention_plan(
    connection: Connection,
    *,
    plan_id: str,
    user_id: str,
) -> InterventionPlan | None:
    """Return one intervention plan row locked for a transaction."""
    row = connection.execute(
        text(
            """SELECT payload FROM intervention_plans
            WHERE plan_id = :plan_id AND user_id = :user_id
            FOR UPDATE"""
        ),
        {"plan_id": plan_id, "user_id": user_id},
    ).mappings().first()
    return InterventionPlan.model_validate(row["payload"]) if row else None


def _update_intervention_plan(connection: Connection, plan: InterventionPlan) -> None:
    """Persist an intervention plan inside an existing transaction."""
    connection.execute(
        text(
            """UPDATE intervention_plans
            SET status = :status,
                session_id = :session_id,
                payload = CAST(:payload AS jsonb),
                updated_at = :updated_at
            WHERE plan_id = :plan_id AND user_id = :user_id"""
        ),
        {
            "status": plan.status,
            "session_id": plan.session_id,
            "payload": plan.model_dump_json(),
            "updated_at": plan.updated_at,
            "plan_id": plan.plan_id,
            "user_id": plan.user_id,
        },
    )


def _sync_plan_after_protocol_response(
    *,
    connection: Connection,
    user_id: str,
    plan_id: str,
    protocol_status: ProtocolStatus,
    updated_at: datetime,
) -> None:
    """Update a linked intervention plan after approval or rejection."""
    plan = _locked_intervention_plan(connection, plan_id=plan_id, user_id=user_id)
    if plan is None:
        return
    if protocol_status == ProtocolStatus.APPROVED:
        updated_steps = [
            step.model_copy(
                update={"status": "completed", "result_summary": "Consent approved."}
            )
            if step.requires_consent
            else step
            for step in plan.steps
        ]
        updated = plan.model_copy(
            update={"status": "active", "steps": updated_steps, "updated_at": updated_at}
        )
        _update_intervention_plan(connection, updated)
        return
    if protocol_status == ProtocolStatus.REJECTED:
        updated_steps = [
            step.model_copy(
                update={
                    "status": "cancelled",
                    "result_summary": "Consent rejected; practice was not started.",
                }
            )
            if step.status in {"in_progress", "pending"}
            else step
            for step in plan.steps
        ]
        updated = plan.model_copy(
            update={
                "status": "cancelled",
                "steps": updated_steps,
                "updated_at": updated_at,
            }
        )
        _update_intervention_plan(connection, updated)


def _complete_linked_action_step(
    *,
    connection: Connection,
    user_id: str,
    plan_id: str,
    result_session_id: str | None,
    result_summary: str,
    updated_at: datetime,
) -> None:
    """Mark the linked action step completed in the same transaction as consume."""
    plan = _locked_intervention_plan(connection, plan_id=plan_id, user_id=user_id)
    if plan is None:
        return
    updated_steps = []
    action_step_updated = False
    for step in plan.steps:
        if step.requires_consent:
            updated_steps.append(
                step.model_copy(
                    update={"status": "completed", "result_summary": "Consent approved."}
                )
            )
            continue
        if step.status == "pending" and not action_step_updated:
            updated_steps.append(
                step.model_copy(
                    update={
                        "status": "completed",
                        "result_summary": result_summary,
                    }
                )
            )
            action_step_updated = True
            continue
        updated_steps.append(step)
    updated = plan.model_copy(
        update={
            "status": "completed",
            "session_id": result_session_id or plan.session_id,
            "steps": updated_steps,
            "updated_at": updated_at,
        }
    )
    _update_intervention_plan(connection, updated)
