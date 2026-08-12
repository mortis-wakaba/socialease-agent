"""Persist explicit module-completion evidence without LLM extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import logging

from app.memory.commit_service import EpisodicMemoryCommitter
from app.memory.long_term_repository import LongTermMemoryRepository
from app.memory.metadata_contract import (
    MemoryOutcomeCode,
    MemoryWriteMetadata,
    outcome_context_tags,
)
from app.memory.policy_engine import MemoryPolicyEngine
from app.memory.settings_store import UserMemorySettingsRepository
from app.models import RiskLevel
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryPolicyAction,
    MemoryProposal,
    MemoryProposalOperation,
    MemoryType,
)


logger = logging.getLogger(__name__)


class CompletedPracticeMemoryWriter:
    """Write only explicit, structured completion events under user consent."""

    def __init__(
        self,
        *,
        memory_repository: LongTermMemoryRepository,
        settings_repository: UserMemorySettingsRepository,
        policy_engine: MemoryPolicyEngine | None = None,
    ) -> None:
        self._settings_repository = settings_repository
        self._policy_engine = policy_engine or MemoryPolicyEngine()
        self._committer = EpisodicMemoryCommitter(memory_repository)

    async def record_completed_practice(
        self,
        *,
        user_id: str,
        source_id: str,
        safe_summary: str,
        metadata: MemoryWriteMetadata,
        occurred_at: datetime | None = None,
    ) -> EpisodicMemoryRecord | None:
        """Commit one completed action, or safely no-op when policy disallows it."""
        if (
            metadata.evidence_type.value != "completed_product_action"
            or metadata.outcome_code != MemoryOutcomeCode.COMPLETED
        ):
            raise ValueError("completed writer requires explicit completion evidence")
        timestamp = occurred_at or datetime.now(UTC)
        try:
            settings = await self._settings_repository.get(user_id)
            if not settings.consent_state.consent_to_practice_summary:
                return None
            if MemoryType.PRACTICE_EXPERIENCE.value in {
                item.value for item in settings.disabled_memory_types
            }:
                return None
            proposal = MemoryProposal(
                operation=MemoryProposalOperation.ADD,
                memory_type=MemoryType.PRACTICE_EXPERIENCE,
                summary=safe_summary,
                scenario_type=metadata.scenario_type,
                scenario_id=metadata.scenario_id,
                practice_thread_id=metadata.practice_thread_id,
                skill_codes=metadata.skill_codes,
                context_tags=outcome_context_tags(metadata),
                source_type=metadata.source_type,
                source_id=source_id,
                evidence_type=metadata.evidence_type,
                confidence=1.0,
                occurred_at=timestamp,
            )
            decision = self._policy_engine.decide(
                proposal,
                consent_state=settings.consent_state,
                risk_level=RiskLevel.LOW,
            )
            if (
                decision.action != MemoryPolicyAction.AUTO_COMMIT
                or decision.safe_summary is None
            ):
                return None
            memory, _ = await self._committer.commit(
                user_id=user_id,
                proposal=proposal,
                safe_summary=decision.safe_summary,
                reason_code=decision.reason.value,
                timestamp=timestamp,
                idempotency_key=_completion_idempotency_key(
                    user_id=user_id,
                    source_type=metadata.source_type.value,
                    source_id=source_id,
                ),
            )
            return memory
        except Exception as error:
            logger.warning(
                "Completed practice memory write failed: %s",
                error.__class__.__name__,
            )
            return None


def _completion_idempotency_key(
    *,
    user_id: str,
    source_type: str,
    source_id: str,
) -> str:
    """Identify one owner-scoped product event without using its summary."""
    material = "\0".join((user_id, source_type, source_id, "completed_practice"))
    return sha256(material.encode("utf-8")).hexdigest()
