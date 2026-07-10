"""Tests for consent protocol lifecycle rules."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.engine import connect
from app.db.session import initialize_database
from app.protocols.service import ProtocolService
from app.services.retention_service import retention_service
from app.safety.actions import HarnessAction


def test_expired_protocol_cannot_be_approved() -> None:
    service = ProtocolService()
    protocol = service.create_consent_request(
        user_id="expired_protocol_user",
        harness_action=HarnessAction.START_ROLEPLAY,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id=None,
        request_hash="expired-hash",
        ttl_seconds=-1,
    )

    response = service.respond(
        protocol_id=protocol.protocol_id,
        user_id="expired_protocol_user",
        approved=True,
    )

    assert response is not None
    assert response.status == "expired"
    assert not service.is_approved_for_action(
        protocol_id=protocol.protocol_id,
        user_id="expired_protocol_user",
        harness_action=HarnessAction.START_ROLEPLAY,
        request_hash="expired-hash",
        session_id=None,
    )


def test_protocol_consume_is_atomic_under_concurrent_attempts() -> None:
    service = ProtocolService()
    user_id = f"concurrent_protocol_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id=None,
        request_hash="concurrent-hash",
    )
    approved = service.respond(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        approved=True,
    )
    assert approved is not None
    assert approved.status == "approved"

    def consume_once() -> bool:
        consumed = service.consume_for_action(
            protocol_id=protocol.protocol_id,
            user_id=user_id,
            harness_action=HarnessAction.START_ROLEPLAY,
            request_hash="concurrent-hash",
            session_id=None,
        )
        return consumed is not None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: consume_once(), range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7


def test_retention_service_expires_pending_protocols() -> None:
    service = ProtocolService()
    user_id = f"retention_protocol_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.CREATE_EXPOSURE_PLAN,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id=None,
        request_hash="retention-hash",
        ttl_seconds=-1,
    )

    expired_count = retention_service.expire_pending_protocols(
        now=datetime.now(timezone.utc),
    )
    record = service.store.get_for_user(protocol.protocol_id, user_id)

    assert expired_count >= 1
    assert record is not None
    assert record.status == "expired"


def test_retention_service_deletes_records_past_retention_window() -> None:
    initialize_database()
    user_id = f"retention_delete_user_{uuid4().hex}"
    run_id = f"run_{uuid4().hex}"
    protocol_id = f"protocol_{uuid4().hex}"
    plan_id = f"plan_{uuid4().hex}"
    old = datetime.now(timezone.utc) - timedelta(days=45)
    now = datetime.now(timezone.utc)

    with connect() as connection:
        connection.execute(
            "INSERT INTO runs (run_id, user_id, payload, created_at) VALUES (?, ?, ?, ?)",
            (run_id, user_id, "{}", old.isoformat()),
        )
        connection.execute(
            """INSERT INTO protocols
            (protocol_id, user_id, protocol_type, status, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                protocol_id,
                user_id,
                "consent_request",
                "consumed",
                "{}",
                old.isoformat(),
                old.isoformat(),
            ),
        )
        connection.execute(
            """INSERT INTO intervention_plans
            (plan_id, user_id, session_id, status, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                plan_id,
                user_id,
                "retention-session",
                "completed",
                "{}",
                old.isoformat(),
                old.isoformat(),
            ),
        )

    result = retention_service.run_once(
        now=now,
        abandoned_plan_minutes=60,
        trace_retention_days=30,
        protocol_retention_days=30,
    )

    with connect() as connection:
        run_row = connection.execute(
            "SELECT run_id FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        protocol_row = connection.execute(
            "SELECT protocol_id FROM protocols WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchone()
        plan_row = connection.execute(
            "SELECT plan_id FROM intervention_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()

    assert result.deleted_raw_traces >= 1
    assert result.deleted_protocol_records >= 1
    assert result.deleted_intervention_plans >= 1
    assert run_row is None
    assert protocol_row is None
    assert plan_row is None
