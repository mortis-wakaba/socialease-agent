"""User-controlled inspection and lifecycle operations for agent memory."""

from datetime import datetime, timezone

from app.db.factory import repository_factory
from app.memory.commit_service import EpisodicMemoryCommitter
from app.memory.identity import memory_content_hash, memory_idempotency_key
from app.memory.long_term_repository import (
    InvalidMemoryTransitionError,
    LongTermMemoryRepository,
    MemoryConflictError,
    MemoryNotFoundError,
)
from app.memory.policy_engine import MemoryPolicyEngine
from app.memory.proposal_repository import MemoryProposalRepository
from app.memory.settings_store import UserMemorySettingsRepository
from app.models import RiskLevel
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvent,
    MemoryEventType,
    MemoryEvidenceType,
    MemoryPolicyAction,
    MemoryProposal,
    MemoryProposalStatus,
    MemoryRecordStatus,
    MemorySourceType,
    PendingMemoryProposalRecord,
    PracticeThreadStatus,
)
from app.models_memory_center import (
    EpisodicMemoryView,
    MemoryCenterResponse,
    MemoryMutationResponse,
    MemoryProposalDecisionResponse,
    MemoryProposalListResponse,
    MemoryProposalView,
    StableMemoryView,
)
from app.models_memory import AgentMemoryType
from app.services.memory_doctor_service import MemoryDoctorService


class MemoryCenterService:
    """Coordinate owner-scoped memory views and deterministic mutations."""

    def __init__(
        self,
        *,
        memory_repository: LongTermMemoryRepository | None = None,
        proposal_repository: MemoryProposalRepository | None = None,
        settings_repository: UserMemorySettingsRepository | None = None,
        policy_engine: MemoryPolicyEngine | None = None,
        doctor_service: MemoryDoctorService | None = None,
    ) -> None:
        factory = repository_factory()
        self.memory_repository = (
            memory_repository or factory.long_term_memory_repository()
        )
        self.committer = EpisodicMemoryCommitter(self.memory_repository)
        self.proposal_repository = (
            proposal_repository or factory.memory_proposal_repository()
        )
        self.settings_repository = (
            settings_repository or factory.user_memory_settings_repository()
        )
        self.policy_engine = policy_engine or MemoryPolicyEngine()
        self.doctor_service = doctor_service or MemoryDoctorService(
            memory_repository=self.memory_repository,
            proposal_repository=self.proposal_repository,
            settings_repository=self.settings_repository,
        )

    async def snapshot(self, user_id: str) -> MemoryCenterResponse:
        """Return a bounded snapshot with stable, working, and episodic layers."""
        timestamp = datetime.now(timezone.utc)
        settings = await self.settings_repository.get(user_id)
        memories = await self.memory_repository.list_memories(user_id, limit=500)
        checkpoints = await self.memory_repository.list_checkpoints(
            user_id,
            limit=500,
        )
        proposals = await self.proposal_repository.list_pending(
            user_id,
            limit=500,
        )
        events = await self.memory_repository.list_events(
            user_id=user_id,
            limit=500,
        )
        return MemoryCenterResponse(
            user_id=user_id,
            stable_memory=StableMemoryView(
                consent_state=settings.consent_state,
                practice_preferences=settings.practice_preferences,
                onboarding_profile=settings.onboarding_profile,
                disabled_memory_types=settings.disabled_memory_types,
            ),
            active_threads=[
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.status
                in {PracticeThreadStatus.ACTIVE, PracticeThreadStatus.PAUSED}
            ][:100],
            memories=[
                _memory_view(record, events=events) for record in memories
            ],
            pending_proposals=[
                _proposal_view(record)
                for record in proposals[:100]
                if record.expires_at > timestamp
            ],
            doctor=self.doctor_service.diagnose_loaded(
                user_id=user_id,
                memories=memories,
                checkpoints=checkpoints,
                proposals=proposals,
                settings=settings,
                now=timestamp,
            ),
        )

    async def set_type_personalization(
        self,
        *,
        user_id: str,
        memory_type: AgentMemoryType,
        enabled: bool,
    ) -> list[AgentMemoryType]:
        """Enable or disable one category without deleting its records."""
        current = await self.settings_repository.get(user_id)
        disabled = list(current.disabled_memory_types)
        if enabled:
            disabled = [item for item in disabled if item != memory_type]
        elif memory_type not in disabled:
            disabled.append(memory_type)
        disabled.sort(key=lambda item: item.value)
        saved = await self.settings_repository.save(
            user_id=user_id,
            disabled_memory_types=disabled,
        )
        return saved.disabled_memory_types

    async def list_proposals(
        self,
        user_id: str,
    ) -> MemoryProposalListResponse:
        """Return only current, unexpired pending candidates."""
        timestamp = datetime.now(timezone.utc)
        return MemoryProposalListResponse(
            user_id=user_id,
            proposals=[
                _proposal_view(record)
                for record in await self.proposal_repository.list_pending(
                    user_id,
                    limit=100,
                )
                if record.expires_at > timestamp
            ],
        )

    async def edit(
        self,
        *,
        user_id: str,
        memory_id: str,
        summary: str,
        expected_version: int,
    ) -> MemoryMutationResponse:
        """Validate and update one user-confirmed summary."""
        current = await self._require_memory(
            user_id=user_id,
            memory_id=memory_id,
        )
        proposal = MemoryProposal(
            memory_type=current.memory_type,
            summary=summary,
            scenario_type=current.scenario_type,
            source_type=MemorySourceType.USER_CONFIRMED,
            source_id=current.memory_id,
            evidence_type=MemoryEvidenceType.USER_CONFIRMED,
            confidence=1.0,
            occurred_at=current.occurred_at,
        )
        decision = self.policy_engine.decide(
            proposal,
            consent_state=(
                await self.settings_repository.get(user_id)
            ).consent_state,
            risk_level=RiskLevel.LOW,
        )
        if (
            decision.action == MemoryPolicyAction.REJECT
            or decision.safe_summary is None
        ):
            raise ValueError(
                f"memory_summary_rejected:{decision.reason.value}"
            )
        safe_summary = decision.safe_summary
        updated = await self.memory_repository.update_memory_summary(
            memory_id=memory_id,
            user_id=user_id,
            expected_version=expected_version,
            summary=safe_summary,
            content_hash=memory_content_hash(safe_summary),
            idempotency_key=memory_idempotency_key(
                user_id=user_id,
                source_type=current.source_type.value,
                memory_type=current.memory_type.value,
                summary=safe_summary,
            ),
            reason_code="user_edited",
        )
        return MemoryMutationResponse(
            user_id=user_id,
            memory=_memory_view(
                updated,
                events=await self.memory_repository.list_events(
                    user_id=user_id,
                    subject_id=memory_id,
                    limit=100,
                ),
            ),
        )

    async def archive(
        self,
        *,
        user_id: str,
        memory_id: str,
        expected_version: int,
    ) -> MemoryMutationResponse:
        """Archive one memory so ordinary retrieval no longer uses it."""
        return await self._transition(
            user_id=user_id,
            memory_id=memory_id,
            expected_version=expected_version,
            target_status=MemoryRecordStatus.ARCHIVED,
            reason_code="user_archived",
        )

    async def restore(
        self,
        *,
        user_id: str,
        memory_id: str,
        expected_version: int,
    ) -> MemoryMutationResponse:
        """Restore one inactive or archived memory to active use."""
        return await self._transition(
            user_id=user_id,
            memory_id=memory_id,
            expected_version=expected_version,
            target_status=MemoryRecordStatus.ACTIVE,
            reason_code="user_restored",
        )

    async def delete(
        self,
        *,
        user_id: str,
        memory_id: str,
        expected_version: int,
    ) -> MemoryMutationResponse:
        """Physically delete one memory body and retain content-free audit."""
        await self.memory_repository.delete_memory(
            memory_id=memory_id,
            user_id=user_id,
            expected_version=expected_version,
            reason_code="user_deleted",
        )
        return MemoryMutationResponse(user_id=user_id, deleted=True)

    async def confirm_proposal(
        self,
        *,
        user_id: str,
        proposal_id: str,
        expected_version: int,
    ) -> MemoryProposalDecisionResponse:
        """Commit one explicitly confirmed candidate and erase proposal content."""
        proposal = await self._require_proposal(
            user_id=user_id,
            proposal_id=proposal_id,
            expected_version=expected_version,
        )
        timestamp = datetime.now(timezone.utc)
        if proposal.expires_at <= timestamp:
            raise MemoryConflictError("pending memory proposal has expired")
        settings = await self.settings_repository.get(user_id)
        if proposal.memory_type.value in {
            memory_type.value for memory_type in settings.disabled_memory_types
        }:
            raise ValueError("memory_proposal_rejected:memory_type_disabled")
        confirmed_proposal = MemoryProposal(
            proposal_id=proposal.proposal_id,
            memory_type=proposal.memory_type,
            summary=proposal.summary,
            scenario_type=proposal.scenario_type,
            scenario_id=proposal.scenario_id,
            practice_thread_id=proposal.practice_thread_id,
            skill_codes=proposal.skill_codes,
            context_tags=proposal.context_tags,
            source_type=proposal.source_type,
            source_id=proposal.source_id,
            evidence_type=MemoryEvidenceType.USER_CONFIRMED,
            confidence=1.0,
            occurred_at=proposal.occurred_at,
        )
        decision = self.policy_engine.decide(
            confirmed_proposal,
            consent_state=settings.consent_state,
            risk_level=RiskLevel.LOW,
        )
        if (
            decision.action == MemoryPolicyAction.REJECT
            or decision.safe_summary is None
        ):
            raise ValueError(
                f"memory_proposal_rejected:{decision.reason.value}"
            )
        memory, _ = await self.committer.commit(
            user_id=user_id,
            proposal=confirmed_proposal,
            safe_summary=decision.safe_summary,
            reason_code="user_confirmed_proposal",
            timestamp=timestamp,
            idempotency_key=proposal.idempotency_key,
            evidence_type=MemoryEvidenceType.USER_CONFIRMED,
            confidence=1.0,
        )
        await self.proposal_repository.consume_pending(
            user_id=user_id,
            proposal_id=proposal_id,
            expected_version=expected_version,
            target_status=MemoryProposalStatus.CONFIRMED,
            reason_code="user_confirmed",
            changed_at=timestamp,
        )
        return MemoryProposalDecisionResponse(
            user_id=user_id,
            proposal_id=proposal_id,
            status=MemoryProposalStatus.CONFIRMED,
            memory=_memory_view(
                memory,
                events=await self.memory_repository.list_events(
                    user_id=user_id,
                    subject_id=memory.memory_id,
                    limit=100,
                ),
            ),
        )

    async def reject_proposal(
        self,
        *,
        user_id: str,
        proposal_id: str,
        expected_version: int,
    ) -> MemoryProposalDecisionResponse:
        """Reject a pending candidate and erase its body."""
        await self._require_proposal(
            user_id=user_id,
            proposal_id=proposal_id,
            expected_version=expected_version,
        )
        await self.proposal_repository.consume_pending(
            user_id=user_id,
            proposal_id=proposal_id,
            expected_version=expected_version,
            target_status=MemoryProposalStatus.REJECTED,
            reason_code="user_rejected",
            changed_at=datetime.now(timezone.utc),
        )
        return MemoryProposalDecisionResponse(
            user_id=user_id,
            proposal_id=proposal_id,
            status=MemoryProposalStatus.REJECTED,
        )

    async def _transition(
        self,
        *,
        user_id: str,
        memory_id: str,
        expected_version: int,
        target_status: MemoryRecordStatus,
        reason_code: str,
    ) -> MemoryMutationResponse:
        updated = await self.memory_repository.transition_memory(
            memory_id=memory_id,
            user_id=user_id,
            expected_version=expected_version,
            target_status=target_status,
            reason_code=reason_code,
        )
        return MemoryMutationResponse(
            user_id=user_id,
            memory=_memory_view(
                updated,
                events=await self.memory_repository.list_events(
                    user_id=user_id,
                    subject_id=memory_id,
                    limit=100,
                ),
            ),
        )

    async def _require_memory(
        self,
        *,
        user_id: str,
        memory_id: str,
    ) -> EpisodicMemoryRecord:
        record = await self.memory_repository.get_memory(memory_id, user_id)
        if record is None:
            raise MemoryNotFoundError(
                "user-scoped episodic memory was not found"
            )
        return record

    async def _require_proposal(
        self,
        *,
        user_id: str,
        proposal_id: str,
        expected_version: int,
    ) -> PendingMemoryProposalRecord:
        record = await self.proposal_repository.get_for_user(
            proposal_id,
            user_id,
        )
        if record is None:
            raise MemoryNotFoundError(
                "user-scoped memory proposal was not found"
            )
        if record.version != expected_version:
            raise MemoryConflictError(
                "pending memory proposal was changed concurrently"
            )
        return record


def _memory_view(
    record: EpisodicMemoryRecord,
    *,
    events: list[MemoryEvent],
) -> EpisodicMemoryView:
    commit_event = next(
        (
            event
            for event in events
            if event.subject_id == record.memory_id
            and event.event_type == MemoryEventType.MEMORY_COMMITTED
        ),
        None,
    )
    return EpisodicMemoryView(
        memory_id=record.memory_id,
        memory_type=record.memory_type,
        summary=record.summary,
        scenario_type=record.scenario_type,
        scenario_id=record.scenario_id,
        practice_thread_id=record.practice_thread_id,
        skill_codes=record.skill_codes,
        context_tags=record.context_tags,
        source_type=record.source_type,
        evidence_type=record.evidence_type,
        confidence=record.confidence,
        status=record.status,
        saved_reason=(
            commit_event.reason_code if commit_event is not None else "unknown"
        ),
        occurred_at=record.occurred_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_retrieved_at=record.last_retrieved_at,
        expires_at=record.expires_at,
        version=record.version,
    )


def _proposal_view(record: PendingMemoryProposalRecord) -> MemoryProposalView:
    return MemoryProposalView(
        proposal_id=record.proposal_id,
        memory_type=record.memory_type,
        summary=record.summary,
        scenario_type=record.scenario_type,
        scenario_id=record.scenario_id,
        practice_thread_id=record.practice_thread_id,
        skill_codes=record.skill_codes,
        context_tags=record.context_tags,
        source_type=record.source_type,
        evidence_type=record.evidence_type,
        confidence=record.confidence,
        status=record.status,
        saved_reason=record.policy_reason,
        occurred_at=record.occurred_at,
        created_at=record.created_at,
        expires_at=record.expires_at,
        version=record.version,
    )
memory_center_service = MemoryCenterService()


__all__ = [
    "InvalidMemoryTransitionError",
    "MemoryCenterService",
    "MemoryConflictError",
    "MemoryNotFoundError",
    "memory_center_service",
]
