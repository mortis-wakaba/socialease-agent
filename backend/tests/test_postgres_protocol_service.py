"""Integration tests for ProtocolService with the PostgreSQL repository adapter."""

from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.db.postgres.protocol_repository import PostgresProtocolRepository
from app.db.postgres.intervention_plan_repository import PostgresInterventionPlanRepository
from app.models_intervention import InterventionStep
from app.models_protocols import ProtocolStatus
from app.protocols.service import ProtocolService
from app.safety.actions import HarnessAction


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests.",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply Alembic migrations to the configured test database."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    command.upgrade(config, "head")


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> ProtocolService:
    """Return a ProtocolService using the configured PostgreSQL adapter."""
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("SOCIALEASE_DATABASE_URL", TEST_DATABASE_URL)
    service = ProtocolService()
    assert isinstance(service.store, PostgresProtocolRepository)
    return service


@pytest.fixture
def plan_repository() -> PostgresInterventionPlanRepository:
    """Return a PostgreSQL intervention plan repository for tests."""
    assert TEST_DATABASE_URL is not None
    return PostgresInterventionPlanRepository(database_url=TEST_DATABASE_URL)


def test_postgres_protocol_service_approve_and_consume(service: ProtocolService) -> None:
    user_id = f"pg_service_user_{uuid4().hex}"
    request_hash = f"pg-service-hash-{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id="pg-service-session",
        request_hash=request_hash,
    )

    approved = service.respond(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        approved=True,
    )
    allowed = service.is_approved_for_action(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        request_hash=request_hash,
        session_id="pg-service-session",
    )
    consumed = service.consume_for_action(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        request_hash=request_hash,
        session_id="pg-service-session",
    )
    replay = service.consume_for_action(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        request_hash=request_hash,
        session_id="pg-service-session",
    )

    assert approved is not None
    assert approved.status == ProtocolStatus.APPROVED
    assert allowed is True
    assert consumed is not None
    assert consumed.status == ProtocolStatus.CONSUMED
    assert replay is None


def test_postgres_protocol_service_rejects_wrong_owner(service: ProtocolService) -> None:
    user_id = f"pg_owner_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.CREATE_EXPOSURE_PLAN,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id=None,
        request_hash="pg-owner-hash",
    )

    wrong_owner_response = service.respond(
        protocol_id=protocol.protocol_id,
        user_id=f"wrong_owner_{uuid4().hex}",
        approved=True,
    )
    original = service.store.get_for_user(protocol.protocol_id, user_id)

    assert wrong_owner_response is None
    assert original is not None
    assert original.status == ProtocolStatus.PENDING


def test_postgres_protocol_service_expires_pending(service: ProtocolService) -> None:
    user_id = f"pg_expired_service_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.CREATE_WORKSHEET,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id=None,
        request_hash="pg-expired-service-hash",
        ttl_seconds=-1,
    )

    expired_count = service.expire_pending_protocols(now=datetime.now(timezone.utc))
    fetched = service.store.get_for_user(protocol.protocol_id, user_id)

    assert expired_count >= 1
    assert fetched is not None
    assert fetched.status == ProtocolStatus.EXPIRED


def test_postgres_protocol_approval_updates_linked_plan_in_transaction(
    service: ProtocolService,
    plan_repository: PostgresInterventionPlanRepository,
) -> None:
    user_id = f"pg_plan_approve_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id="pg-plan-approve-session",
        request_hash="pg-plan-approve-hash",
    )
    plan = plan_repository.create(
        user_id=user_id,
        session_id="pg-plan-approve-session",
        status="pending_consent",
        protocol_id=protocol.protocol_id,
        steps=[
            _step("ask_consent", requires_consent=True, status="in_progress"),
            _step("start_roleplay", status="pending"),
        ],
    )
    linked = service.link_intervention_plan(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        intervention_plan_id=plan.plan_id,
    )

    approved = service.respond(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        approved=True,
    )
    updated_plan = plan_repository.get_by_id_for_user(plan.plan_id, user_id)

    assert linked is not None
    assert approved is not None
    assert approved.status == ProtocolStatus.APPROVED
    assert updated_plan is not None
    assert updated_plan.status == "active"
    assert updated_plan.steps[0].status == "completed"
    assert updated_plan.steps[1].status == "pending"


def test_postgres_protocol_rejection_updates_linked_plan_in_transaction(
    service: ProtocolService,
    plan_repository: PostgresInterventionPlanRepository,
) -> None:
    user_id = f"pg_plan_reject_user_{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.CREATE_EXPOSURE_PLAN,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id="pg-plan-reject-session",
        request_hash="pg-plan-reject-hash",
    )
    plan = plan_repository.create(
        user_id=user_id,
        session_id="pg-plan-reject-session",
        status="pending_consent",
        protocol_id=protocol.protocol_id,
        steps=[
            _step("ask_consent", requires_consent=True, status="in_progress"),
            _step("create_ladder", status="pending"),
        ],
    )
    service.link_intervention_plan(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        intervention_plan_id=plan.plan_id,
    )

    rejected = service.respond(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        approved=False,
    )
    updated_plan = plan_repository.get_by_id_for_user(plan.plan_id, user_id)

    assert rejected is not None
    assert rejected.status == ProtocolStatus.REJECTED
    assert updated_plan is not None
    assert updated_plan.status == "cancelled"
    assert [step.status for step in updated_plan.steps] == ["cancelled", "cancelled"]


def test_postgres_protocol_consume_can_complete_linked_plan_in_transaction(
    service: ProtocolService,
    plan_repository: PostgresInterventionPlanRepository,
) -> None:
    user_id = f"pg_plan_consume_user_{uuid4().hex}"
    request_hash = f"pg-plan-consume-hash-{uuid4().hex}"
    protocol = service.create_consent_request(
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        reason="test consent",
        required_protocol="explicit_consent",
        session_id="pg-plan-consume-session",
        request_hash=request_hash,
    )
    plan = plan_repository.create(
        user_id=user_id,
        session_id="pg-plan-consume-session",
        status="pending_consent",
        protocol_id=protocol.protocol_id,
        steps=[
            _step("ask_consent", requires_consent=True, status="in_progress"),
            _step("start_roleplay", status="pending"),
        ],
    )
    service.link_intervention_plan(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        intervention_plan_id=plan.plan_id,
    )
    approved = service.respond(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        approved=True,
    )
    assert approved is not None

    consumed = service.consume_for_action(
        protocol_id=protocol.protocol_id,
        user_id=user_id,
        harness_action=HarnessAction.START_ROLEPLAY,
        request_hash=request_hash,
        session_id="pg-plan-consume-session",
        result_session_id="roleplay-result-session",
        result_summary="Executed start_roleplay.",
    )
    updated_plan = plan_repository.get_by_id_for_user(plan.plan_id, user_id)

    assert consumed is not None
    assert consumed.status == ProtocolStatus.CONSUMED
    assert updated_plan is not None
    assert updated_plan.status == "completed"
    assert updated_plan.session_id == "roleplay-result-session"
    assert [step.status for step in updated_plan.steps] == ["completed", "completed"]


def _step(
    step_id: str,
    *,
    requires_consent: bool = False,
    status: str = "pending",
) -> InterventionStep:
    return InterventionStep(
        step_id=step_id,
        title=step_id.replace("_", " "),
        skill="test_skill",
        status=status,  # type: ignore[arg-type]
        requires_consent=requires_consent,
        protocol_id="test_protocol" if requires_consent else None,
    )
