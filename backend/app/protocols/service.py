"""Protocol service used by APIs and the lead harness."""

from datetime import datetime, timedelta, timezone

from app.models_protocols import (
    ProtocolRecord,
    ProtocolStatus,
    ProtocolType,
)
from app.db.factory import repository_factory
from app.protocols.store import ProtocolRepository
from app.safety.actions import HarnessAction


class ProtocolService:
    """Create and validate consent protocols."""

    def __init__(self, store: ProtocolRepository | None = None) -> None:
        self.store = store or repository_factory().protocol_repository()

    async def create_consent_request(
        self,
        *,
        user_id: str,
        harness_action: HarnessAction,
        reason: str,
        required_protocol: str | None,
        session_id: str | None,
        request_hash: str,
        ttl_seconds: int = 30 * 60,
    ) -> ProtocolRecord:
        """Create a pending consent request for a harness action."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        return await self.store.create(
            user_id=user_id,
            protocol_type=ProtocolType.CONSENT_REQUEST,
            session_id=session_id,
            harness_action=harness_action.value,
            request_hash=request_hash,
            expires_at=expires_at,
            payload={
                "harness_action": harness_action.value,
                "reason": reason,
                "required_protocol": required_protocol,
                "request_hash": request_hash,
            },
        )

    async def respond(
        self,
        *,
        protocol_id: str,
        user_id: str,
        approved: bool,
    ) -> ProtocolRecord | None:
        """Approve or reject a pending protocol."""
        transactional_respond = getattr(
            self.store,
            "respond_with_linked_intervention_plan",
            None,
        )
        if transactional_respond is not None:
            return await transactional_respond(
                protocol_id=protocol_id,
                user_id=user_id,
                approved=approved,
                now=datetime.now(timezone.utc),
            )
        record = await self.store.get_for_user(protocol_id, user_id)
        if record is None:
            return None
        if self._is_expired(record):
            return await self.store.set_status(
                protocol_id=protocol_id,
                user_id=user_id,
                status=ProtocolStatus.EXPIRED,
            )
        if record.status != ProtocolStatus.PENDING:
            return record
        status = ProtocolStatus.APPROVED if approved else ProtocolStatus.REJECTED
        updated = await self.store.transition_status(
            protocol_id=protocol_id,
            user_id=user_id,
            expected_status=ProtocolStatus.PENDING,
            next_status=status,
        )
        if updated is not None:
            await self._sync_intervention_plan_after_response(updated)
        return updated

    async def link_intervention_plan(
        self,
        *,
        protocol_id: str,
        user_id: str,
        intervention_plan_id: str,
    ) -> ProtocolRecord | None:
        """Attach a created intervention plan to a protocol payload."""
        record = await self.store.get_for_user(protocol_id, user_id)
        if record is None:
            return None
        payload = {**record.payload, "intervention_plan_id": intervention_plan_id}
        return await self.store.save(
            record.model_copy(update={"payload": payload})
        )

    async def is_approved_for_action(
        self,
        *,
        protocol_id: str | None,
        user_id: str,
        harness_action: HarnessAction,
        request_hash: str,
        session_id: str | None,
    ) -> bool:
        """Return whether the protocol approves this exact harness action."""
        if protocol_id is None:
            return False
        record = await self.store.get_for_user(protocol_id, user_id)
        if record is None:
            return False
        if self._is_expired(record):
            await self.store.set_status(
                protocol_id=protocol_id,
                user_id=user_id,
                status=ProtocolStatus.EXPIRED,
            )
            return False
        if record.status != ProtocolStatus.APPROVED:
            return False
        if record.harness_action != harness_action.value:
            return False
        if record.request_hash != request_hash:
            return False
        if record.session_id is not None and record.session_id != session_id:
            return False
        return True

    async def consume_for_action(
        self,
        *,
        protocol_id: str | None,
        user_id: str,
        harness_action: HarnessAction,
        request_hash: str,
        session_id: str | None,
        result_session_id: str | None = None,
        result_summary: str | None = None,
    ) -> ProtocolRecord | None:
        """Consume a valid approved protocol so it cannot be reused."""
        if protocol_id is None:
            return None
        transactional_consume = getattr(
            self.store,
            "consume_with_linked_intervention_plan",
            None,
        )
        if transactional_consume is not None:
            return await transactional_consume(
                protocol_id=protocol_id,
                user_id=user_id,
                harness_action=harness_action.value,
                request_hash=request_hash,
                session_id=session_id,
                now=datetime.now(timezone.utc),
                result_session_id=result_session_id,
                result_summary=result_summary,
            )
        if not await self.is_approved_for_action(
            protocol_id=protocol_id,
            user_id=user_id,
            harness_action=harness_action,
            request_hash=request_hash,
            session_id=session_id,
        ):
            return None
        return await self.store.transition_status(
            protocol_id=protocol_id,
            user_id=user_id,
            expected_status=ProtocolStatus.APPROVED,
            next_status=ProtocolStatus.CONSUMED,
        )

    async def claim_for_action(
        self,
        *,
        protocol_id: str | None,
        user_id: str,
        harness_action: HarnessAction,
        request_hash: str,
        session_id: str | None,
    ) -> ProtocolRecord | None:
        """Atomically claim an approved protocol before executing its side effect."""
        if protocol_id is None:
            return None
        record = await self.store.get_for_user(protocol_id, user_id)
        if record is None:
            return None
        if self._is_expired(record):
            await self.store.set_status(
                protocol_id=protocol_id,
                user_id=user_id,
                status=ProtocolStatus.EXPIRED,
            )
            return None
        if (
            record.status != ProtocolStatus.APPROVED
            or record.harness_action != harness_action.value
            or record.request_hash != request_hash
            or (
                record.session_id is not None
                and record.session_id != session_id
            )
        ):
            return None
        return await self.store.transition_status(
            protocol_id=protocol_id,
            user_id=user_id,
            expected_status=ProtocolStatus.APPROVED,
            next_status=ProtocolStatus.CONSUMED,
        )

    async def linked_intervention_plan_id(
        self,
        *,
        protocol_id: str | None,
        user_id: str,
    ) -> str | None:
        """Return the intervention plan linked to a protocol, if present."""
        if protocol_id is None:
            return None
        record = await self.store.get_for_user(protocol_id, user_id)
        if record is None:
            return None
        plan_id = record.payload.get("intervention_plan_id")
        return plan_id if isinstance(plan_id, str) else None

    async def expire_pending_protocols(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """Expire pending protocols up to a cutoff timestamp."""
        return await self.store.expire_pending_before(
            now or datetime.now(timezone.utc)
        )

    @staticmethod
    def _is_expired(record: ProtocolRecord) -> bool:
        """Return whether a protocol is past its expiration timestamp."""
        return record.expires_at is not None and record.expires_at <= datetime.now(timezone.utc)

    @staticmethod
    async def _sync_intervention_plan_after_response(
        record: ProtocolRecord,
    ) -> None:
        """Update linked intervention plan after a protocol response."""
        plan_id = record.payload.get("intervention_plan_id")
        if not isinstance(plan_id, str):
            return
        from app.services.intervention_plan_service import intervention_plan_service

        if record.status == ProtocolStatus.APPROVED:
            await intervention_plan_service.mark_consent_approved(
                user_id=record.user_id,
                plan_id=plan_id,
            )
        elif record.status == ProtocolStatus.REJECTED:
            await intervention_plan_service.mark_consent_rejected(
                user_id=record.user_id,
                plan_id=plan_id,
            )


protocol_service = ProtocolService()
