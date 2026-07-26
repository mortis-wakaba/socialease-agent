"""Policy-gated memory extraction and durable write orchestration."""

from datetime import datetime, timedelta, timezone
import hashlib
import logging

from app.memory.long_term_repository import (
    LongTermMemoryRepository,
    MemoryConflictError,
)
from app.memory.policy_engine import MemoryPolicyEngine
from app.memory.proposal_extractor import (
    MemoryExtractionError,
    MemoryProposalExtractor,
)
from app.memory.proposal_repository import MemoryProposalRepository
from app.memory.settings_store import UserMemorySettingsRepository
from app.models import RiskLevel
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryPipelineItemResult,
    MemoryPipelineResult,
    MemoryPolicyAction,
    MemoryPolicyReason,
    MemoryProposal,
    MemoryProposalStatus,
    MemoryRecordStatus,
    MemorySourceType,
    PendingMemoryProposalRecord,
)


MEMORY_CONSENT_VERSION = "practice-summary-v1"
logger = logging.getLogger(__name__)


class MemoryWritePipeline:
    """Let models propose while application policy retains write authority."""

    def __init__(
        self,
        *,
        extractor: MemoryProposalExtractor,
        policy_engine: MemoryPolicyEngine,
        memory_repository: LongTermMemoryRepository,
        proposal_repository: MemoryProposalRepository,
        settings_repository: UserMemorySettingsRepository,
    ) -> None:
        self.extractor = extractor
        self.policy_engine = policy_engine
        self.memory_repository = memory_repository
        self.proposal_repository = proposal_repository
        self.settings_repository = settings_repository

    async def process_messages(
        self,
        *,
        user_id: str,
        messages: list[dict[str, str]],
        source_type: MemorySourceType,
        source_id: str | None,
        occurred_at: datetime,
        risk_level: RiskLevel,
        now: datetime | None = None,
    ) -> MemoryPipelineResult:
        """Process one bounded batch without raising provider/write errors."""
        timestamp = _as_utc(now or datetime.now(timezone.utc))
        occurrence = _as_utc(occurred_at)
        settings = self.settings_repository.get(user_id)
        if risk_level == RiskLevel.CRISIS:
            return MemoryPipelineResult(status="skipped")
        if not settings.consent_state.consent_to_practice_summary:
            return MemoryPipelineResult(status="skipped")
        if not self.extractor.enabled:
            return MemoryPipelineResult(status="skipped")

        try:
            existing_memories = self.memory_repository.list_memories(
                user_id,
                limit=20,
            )
            extracted = await self.extractor.extract(
                messages=messages,
                source_type=source_type,
                source_id=source_id,
                occurred_at=occurrence,
                existing_memories=existing_memories,
            )
        except MemoryExtractionError as error:
            return MemoryPipelineResult(
                status="extraction_failed",
                error_category=error.error_category,
            )
        if not extracted.proposals:
            return MemoryPipelineResult(status="no_candidates")

        items: list[MemoryPipelineItemResult] = []
        write_failures = 0
        explicit_revoke_requested = _has_explicit_revoke_request(messages)
        for proposal in extracted.proposals:
            decision = self.policy_engine.decide(
                proposal,
                consent_state=settings.consent_state,
                risk_level=risk_level,
                explicit_revoke_requested=explicit_revoke_requested,
            )
            if decision.action == MemoryPolicyAction.REJECT:
                try:
                    self.proposal_repository.record_rejection(
                        user_id=user_id,
                        proposal_id=proposal.proposal_id,
                        reason_code=decision.reason.value,
                        created_at=timestamp,
                    )
                except Exception as error:
                    write_failures += 1
                    logger.warning(
                        "Memory rejection audit failed: %s",
                        error.__class__.__name__,
                    )
                items.append(
                    MemoryPipelineItemResult(
                        proposal_id=proposal.proposal_id,
                        action=decision.action,
                        reason=decision.reason,
                    )
                )
                continue
            if decision.safe_summary is None:
                write_failures += 1
                continue
            try:
                if decision.action == MemoryPolicyAction.REVOKE:
                    memory, deduplicated = self._revoke(
                        user_id=user_id,
                        proposal=proposal,
                        changed_at=timestamp,
                    )
                    if memory is None:
                        self.proposal_repository.record_rejection(
                            user_id=user_id,
                            proposal_id=proposal.proposal_id,
                            reason_code=(
                                MemoryPolicyReason.REVOCATION_TARGET_NOT_FOUND.value
                            ),
                            created_at=timestamp,
                        )
                        items.append(
                            MemoryPipelineItemResult(
                                proposal_id=proposal.proposal_id,
                                action=MemoryPolicyAction.REJECT,
                                reason=(
                                    MemoryPolicyReason.REVOCATION_TARGET_NOT_FOUND
                                ),
                            )
                        )
                        continue
                    items.append(
                        MemoryPipelineItemResult(
                            proposal_id=proposal.proposal_id,
                            action=decision.action,
                            reason=decision.reason,
                            memory_id=memory.memory_id,
                            deduplicated=deduplicated,
                        )
                    )
                elif decision.action == MemoryPolicyAction.AUTO_COMMIT:
                    memory, deduplicated = self._commit(
                        user_id=user_id,
                        proposal=proposal,
                        safe_summary=decision.safe_summary,
                        reason_code=decision.reason.value,
                        timestamp=timestamp,
                    )
                    items.append(
                        MemoryPipelineItemResult(
                            proposal_id=proposal.proposal_id,
                            action=decision.action,
                            reason=decision.reason,
                            memory_id=memory.memory_id,
                            deduplicated=deduplicated,
                        )
                    )
                else:
                    pending, deduplicated = self._save_pending(
                        user_id=user_id,
                        proposal=proposal,
                        safe_summary=decision.safe_summary,
                        policy_reason=decision.reason,
                        timestamp=timestamp,
                    )
                    items.append(
                        MemoryPipelineItemResult(
                            proposal_id=pending.proposal_id,
                            action=decision.action,
                            reason=decision.reason,
                            deduplicated=deduplicated,
                        )
                    )
            except Exception as error:
                write_failures += 1
                logger.warning(
                    "Memory proposal persistence failed: %s",
                    error.__class__.__name__,
                )

        if write_failures:
            return MemoryPipelineResult(
                status="partial_failure" if items else "write_failed",
                items=items,
                error_category="MEMORY_PERSISTENCE_ERROR",
            )
        if any(
            item.action == MemoryPolicyAction.REQUIRE_CONFIRMATION
            for item in items
        ):
            return MemoryPipelineResult(status="confirmation_required", items=items)
        if any(
            item.action
            in {MemoryPolicyAction.AUTO_COMMIT, MemoryPolicyAction.REVOKE}
            for item in items
        ):
            return MemoryPipelineResult(status="committed", items=items)
        return MemoryPipelineResult(status="rejected", items=items)

    def _commit(
        self,
        *,
        user_id: str,
        proposal: MemoryProposal,
        safe_summary: str,
        reason_code: str,
        timestamp: datetime,
    ) -> tuple[EpisodicMemoryRecord, bool]:
        content_hash = _content_hash(safe_summary)
        idempotency_key = _idempotency_key(
            user_id=user_id,
            proposal=proposal,
            safe_summary=safe_summary,
        )
        existing = self.memory_repository.get_memory_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing, True
        record = EpisodicMemoryRecord(
            memory_id=f"memory_{idempotency_key[:32]}",
            user_id=user_id,
            memory_type=proposal.memory_type,
            summary=safe_summary,
            scenario_type=proposal.scenario_type,
            source_type=proposal.source_type,
            source_id=proposal.source_id,
            evidence_type=proposal.evidence_type,
            confidence=proposal.confidence,
            status=MemoryRecordStatus.ACTIVE,
            occurred_at=proposal.occurred_at,
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=_memory_expiry(proposal, timestamp),
            consent_version=MEMORY_CONSENT_VERSION,
            content_hash=content_hash,
            idempotency_key=idempotency_key,
        )
        try:
            return (
                self.memory_repository.create_memory(
                    record,
                    reason_code=reason_code,
                ),
                False,
            )
        except MemoryConflictError:
            existing = self.memory_repository.get_memory_by_idempotency_key(
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            return existing, True

    def _save_pending(
        self,
        *,
        user_id: str,
        proposal: MemoryProposal,
        safe_summary: str,
        policy_reason: MemoryPolicyReason,
        timestamp: datetime,
    ) -> tuple[PendingMemoryProposalRecord, bool]:
        content_hash = _content_hash(safe_summary)
        idempotency_key = _idempotency_key(
            user_id=user_id,
            proposal=proposal,
            safe_summary=safe_summary,
        )
        existing = self.proposal_repository.get_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing, True
        record = PendingMemoryProposalRecord(
            proposal_id=f"proposal_{idempotency_key[:32]}",
            user_id=user_id,
            memory_type=proposal.memory_type,
            summary=safe_summary,
            scenario_type=proposal.scenario_type,
            source_type=proposal.source_type,
            source_id=proposal.source_id,
            evidence_type=proposal.evidence_type,
            confidence=proposal.confidence,
            occurred_at=proposal.occurred_at,
            status=MemoryProposalStatus.PENDING_CONFIRMATION,
            policy_reason=policy_reason,
            content_hash=content_hash,
            idempotency_key=idempotency_key,
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=timestamp + timedelta(days=30),
        )
        try:
            return self.proposal_repository.save_pending(record), False
        except MemoryConflictError:
            existing = self.proposal_repository.get_by_idempotency_key(
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            return existing, True

    def _revoke(
        self,
        *,
        user_id: str,
        proposal: MemoryProposal,
        changed_at: datetime,
    ) -> tuple[EpisodicMemoryRecord | None, bool]:
        content_hash = _content_hash(proposal.summary)
        existing = self.memory_repository.get_memory_by_content_hash(
            user_id=user_id,
            content_hash=content_hash,
        )
        if existing is None:
            return None, False
        if existing.status in {
            MemoryRecordStatus.REVOKED,
            MemoryRecordStatus.SUPERSEDED,
        }:
            return existing, True
        try:
            return (
                self.memory_repository.transition_memory(
                    memory_id=existing.memory_id,
                    user_id=user_id,
                    expected_version=existing.version,
                    target_status=MemoryRecordStatus.REVOKED,
                    reason_code=MemoryPolicyReason.EXPLICIT_REVOCATION_ALLOWED.value,
                    changed_at=changed_at,
                ),
                False,
            )
        except MemoryConflictError:
            current = self.memory_repository.get_memory_by_content_hash(
                user_id=user_id,
                content_hash=content_hash,
            )
            if current is not None and current.status in {
                MemoryRecordStatus.REVOKED,
                MemoryRecordStatus.SUPERSEDED,
            }:
                return current, True
            raise


def _content_hash(summary: str) -> str:
    normalized = " ".join(summary.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _has_explicit_revoke_request(messages: list[dict[str, str]]) -> bool:
    """Require deletion intent from original user text, not model-owned evidence."""
    user_text = "\n".join(
        message.get("content", "")
        for message in messages
        if message.get("role") == "user"
    ).casefold()
    markers = (
        "请忘记",
        "忘掉",
        "不要记住",
        "不要再记",
        "删除这条",
        "删除记忆",
        "移除这条",
        "清除这条",
        "forget this",
        "forget that",
        "delete this memory",
        "remove this memory",
        "do not remember",
        "don't remember",
    )
    return any(marker in user_text for marker in markers)


def _idempotency_key(
    *,
    user_id: str,
    proposal: MemoryProposal,
    safe_summary: str,
) -> str:
    material = "\0".join(
        (
            user_id,
            proposal.source_type.value,
            proposal.memory_type.value,
            " ".join(safe_summary.casefold().split()),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _memory_expiry(
    proposal: MemoryProposal,
    created_at: datetime,
) -> datetime:
    long_lived = proposal.memory_type.value == "helpful_strategy"
    return created_at + timedelta(days=730 if long_lived else 365)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("memory pipeline timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
