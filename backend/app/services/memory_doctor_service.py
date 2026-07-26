"""Read-only, user-scoped quality diagnostics for durable agent memory."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Protocol

from app.db.factory import repository_factory
from app.memory.active_memory_assembler import (
    ActiveMemoryAssembler,
    episodic_types_for_skill,
)
from app.memory.long_term_repository import LongTermMemoryRepository
from app.memory.proposal_repository import MemoryProposalRepository
from app.memory.settings_store import UserMemorySettingsRepository
from app.memory.text_semantics import conflict_overlap
from app.memory.thread_checkpoint_service import project_checkpoint_context
from app.memory.token_estimator import ConservativeTokenEstimator, TokenEstimator
from app.models_active_memory import ActiveMemoryDropReason
from app.models_context import (
    ContextConfidence,
    ContextFieldMetadata,
    ContextValueSource,
    SkillContextProjection,
)
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryRecordStatus,
    MemoryRetrievalDiagnostics,
    MemoryRetrievalHit,
    MemoryRetrievalResult,
    MemoryRetrievalScore,
    MemoryRetrievalStrategy,
    MemorySourceType,
    PendingMemoryProposalRecord,
    PracticeThreadCheckpoint,
    PracticeThreadStatus,
)
from app.models_memory import UserMemorySettings
from app.models_memory_doctor import (
    MemoryDoctorCheck,
    MemoryDoctorCheckStatus,
    MemoryDoctorIssue,
    MemoryDoctorIssueCode,
    MemoryDoctorReport,
    MemoryDoctorSeverity,
    MemoryDoctorSubjectType,
    MemoryDoctorThresholds,
)


class EmbeddingIntegrityInspector(Protocol):
    """Optional production embedding integrity adapter."""

    enabled: bool

    def orphan_subject_hashes(self, *, user_id: str) -> list[str]: ...


class DisabledEmbeddingIntegrityInspector:
    """Explicitly mark embedding checks unavailable while phase five is off."""

    enabled = False

    def orphan_subject_hashes(self, *, user_id: str) -> list[str]:
        del user_id
        return []


class MemoryDoctorService:
    """Detect quality issues without mutating or returning stored content."""

    def __init__(
        self,
        *,
        memory_repository: LongTermMemoryRepository | None = None,
        proposal_repository: MemoryProposalRepository | None = None,
        settings_repository: UserMemorySettingsRepository | None = None,
        token_estimator: TokenEstimator | None = None,
        embedding_inspector: EmbeddingIntegrityInspector | None = None,
        stale_memory_days: int = 180,
        stale_checkpoint_days: int = 180,
        pending_proposal_days: int = 7,
        active_memory_token_budget: int = 512,
        conflict_term_overlap: int = 2,
    ) -> None:
        factory = repository_factory()
        self.memory_repository = (
            memory_repository or factory.long_term_memory_repository()
        )
        self.proposal_repository = (
            proposal_repository or factory.memory_proposal_repository()
        )
        self.settings_repository = (
            settings_repository or factory.user_memory_settings_repository()
        )
        self.token_estimator = token_estimator or ConservativeTokenEstimator()
        self.active_memory_assembler = ActiveMemoryAssembler(
            token_estimator=self.token_estimator,
            token_budget=active_memory_token_budget,
        )
        self.embedding_inspector = (
            embedding_inspector or DisabledEmbeddingIntegrityInspector()
        )
        self.thresholds = MemoryDoctorThresholds(
            stale_memory_days=max(stale_memory_days, 1),
            stale_checkpoint_days=max(stale_checkpoint_days, 1),
            pending_proposal_days=max(pending_proposal_days, 1),
            active_memory_token_budget=self.active_memory_assembler.token_budget,
            conflict_term_overlap=max(conflict_term_overlap, 1),
        )

    def diagnose(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> MemoryDoctorReport:
        """Run all bounded checks inside one exact user repository scope."""
        return self.diagnose_loaded(
            user_id=user_id,
            memories=self.memory_repository.list_memories(user_id, limit=500),
            checkpoints=self.memory_repository.list_checkpoints(
                user_id,
                limit=500,
            ),
            proposals=self.proposal_repository.list_pending(
                user_id,
                limit=500,
            ),
            settings=self.settings_repository.get(user_id),
            now=now,
        )

    def diagnose_loaded(
        self,
        *,
        user_id: str,
        memories: list[EpisodicMemoryRecord],
        checkpoints: list[PracticeThreadCheckpoint],
        proposals: list[PendingMemoryProposalRecord],
        settings: UserMemorySettings,
        now: datetime | None = None,
    ) -> MemoryDoctorReport:
        """Diagnose an already owner-scoped snapshot without querying it twice."""
        if any(memory.user_id != user_id for memory in memories):
            raise ValueError("memory doctor received cross-user memory data")
        if any(checkpoint.user_id != user_id for checkpoint in checkpoints):
            raise ValueError("memory doctor received cross-user checkpoint data")
        if any(proposal.user_id != user_id for proposal in proposals):
            raise ValueError("memory doctor received cross-user proposal data")
        timestamp = _as_utc(now or datetime.now(timezone.utc))
        issues: list[MemoryDoctorIssue] = []
        issues.extend(self._duplicate_issues(memories))
        issues.extend(self._conflict_issues(memories))
        issues.extend(self._stale_memory_issues(memories, now=timestamp))
        issues.extend(
            self._settings_issues(
                memories,
                consent_enabled=(
                    settings.consent_state.consent_to_practice_summary
                ),
                disabled_types={
                    item.value for item in settings.disabled_memory_types
                },
            )
        )
        issues.extend(self._source_and_timestamp_issues(memories, now=timestamp))
        issues.extend(
            self._active_budget_issues(
                memories,
                checkpoints,
                now=timestamp,
            )
        )
        issues.extend(self._stale_checkpoint_issues(checkpoints, now=timestamp))
        issues.extend(self._aged_proposal_issues(proposals, now=timestamp))

        embedding_check, embedding_issues = self._embedding_issues(user_id)
        issues.extend(embedding_issues)
        issues.sort(key=lambda item: (item.code.value, item.issue_id))
        checks = self._checks(issues, embedding_check=embedding_check)
        issues_truncated = len(issues) > 500
        issues = issues[:500]
        return MemoryDoctorReport(
            user_id=user_id,
            generated_at=timestamp,
            scanned_counts={
                "episodic_memories": len(memories),
                "thread_checkpoints": len(checkpoints),
                "pending_proposals": len(proposals),
            },
            thresholds=self.thresholds,
            checks=checks,
            issues=issues,
            issues_truncated=issues_truncated,
        )

    def _duplicate_issues(
        self,
        memories: list[EpisodicMemoryRecord],
    ) -> list[MemoryDoctorIssue]:
        groups: dict[str, list[EpisodicMemoryRecord]] = defaultdict(list)
        for memory in memories:
            if memory.status not in {
                MemoryRecordStatus.REVOKED,
                MemoryRecordStatus.SUPERSEDED,
            }:
                groups[memory.content_hash].append(memory)
        return [
            _issue(
                code=MemoryDoctorIssueCode.DUPLICATE_MEMORY,
                severity=MemoryDoctorSeverity.WARNING,
                subject_type=MemoryDoctorSubjectType.EPISODIC_MEMORY,
                subject_ids=[item.memory_id for item in group],
                metadata={
                    "memory_type": group[0].memory_type.value,
                    "duplicate_count": len(group),
                },
                recommendation_code="review_duplicate_memories",
            )
            for group in groups.values()
            if len(group) > 1
        ]

    def _conflict_issues(
        self,
        memories: list[EpisodicMemoryRecord],
    ) -> list[MemoryDoctorIssue]:
        active = [
            memory
            for memory in memories
            if memory.status == MemoryRecordStatus.ACTIVE
        ]
        issues: list[MemoryDoctorIssue] = []
        for index, left in enumerate(active):
            for right in active[index + 1 :]:
                if (
                    left.memory_type != right.memory_type
                    or left.scenario_type != right.scenario_type
                ):
                    continue
                overlap = conflict_overlap(left.summary, right.summary)
                if overlap < self.thresholds.conflict_term_overlap:
                    continue
                issues.append(
                    _issue(
                        code=MemoryDoctorIssueCode.CONFLICTING_MEMORY,
                        severity=MemoryDoctorSeverity.ACTION_REQUIRED,
                        subject_type=MemoryDoctorSubjectType.EPISODIC_MEMORY,
                        subject_ids=[left.memory_id, right.memory_id],
                        metadata={
                            "memory_type": left.memory_type.value,
                            "scenario_type": (
                                left.scenario_type or "none"
                            ),
                            "overlap_term_count": overlap,
                        },
                        recommendation_code="review_conflicting_memories",
                    )
                )
                if len(issues) >= 100:
                    return issues
        return issues

    def _stale_memory_issues(
        self,
        memories: list[EpisodicMemoryRecord],
        *,
        now: datetime,
    ) -> list[MemoryDoctorIssue]:
        cutoff = now - timedelta(days=self.thresholds.stale_memory_days)
        result = []
        for memory in memories:
            last_use = memory.last_retrieved_at or memory.occurred_at
            if (
                memory.status == MemoryRecordStatus.ACTIVE
                and _as_utc(last_use) < cutoff
            ):
                result.append(
                    _issue(
                        code=MemoryDoctorIssueCode.STALE_UNUSED_MEMORY,
                        severity=MemoryDoctorSeverity.INFO,
                        subject_type=MemoryDoctorSubjectType.EPISODIC_MEMORY,
                        subject_ids=[memory.memory_id],
                        metadata={
                            "memory_type": memory.memory_type.value,
                            "unused_days": max(
                                0,
                                (now - _as_utc(last_use)).days,
                            ),
                        },
                        recommendation_code="consider_archiving_stale_memory",
                    )
                )
        return result

    def _settings_issues(
        self,
        memories: list[EpisodicMemoryRecord],
        *,
        consent_enabled: bool,
        disabled_types: set[str],
    ) -> list[MemoryDoctorIssue]:
        active = [
            memory
            for memory in memories
            if memory.status == MemoryRecordStatus.ACTIVE
        ]
        issues: list[MemoryDoctorIssue] = []
        if active and not consent_enabled:
            issues.append(
                _issue(
                    code=MemoryDoctorIssueCode.CONSENT_INACTIVE_MEMORY,
                    severity=MemoryDoctorSeverity.INFO,
                    subject_type=MemoryDoctorSubjectType.USER_MEMORY_SETTINGS,
                    subject_ids=[memory.memory_id for memory in active],
                    metadata={"active_memory_count": len(active)},
                    recommendation_code="review_retained_memories_after_consent",
                )
            )
        for memory_type in sorted(disabled_types):
            affected = [
                memory
                for memory in active
                if memory.memory_type.value == memory_type
            ]
            if affected:
                issues.append(
                    _issue(
                        code=(
                            MemoryDoctorIssueCode.TYPE_PERSONALIZATION_DISABLED
                        ),
                        severity=MemoryDoctorSeverity.INFO,
                        subject_type=MemoryDoctorSubjectType.USER_MEMORY_SETTINGS,
                        subject_ids=[memory.memory_id for memory in affected],
                        metadata={
                            "memory_type": memory_type,
                            "retained_count": len(affected),
                        },
                        recommendation_code="review_disabled_type_records",
                    )
                )
        return issues

    def _source_and_timestamp_issues(
        self,
        memories: list[EpisodicMemoryRecord],
        *,
        now: datetime,
    ) -> list[MemoryDoctorIssue]:
        issues = []
        for memory in memories:
            if (
                memory.source_type != MemorySourceType.USER_CONFIRMED
                and memory.source_id is None
            ):
                issues.append(
                    _issue(
                        code=MemoryDoctorIssueCode.SOURCE_REFERENCE_MISSING,
                        severity=MemoryDoctorSeverity.WARNING,
                        subject_type=MemoryDoctorSubjectType.EPISODIC_MEMORY,
                        subject_ids=[memory.memory_id],
                        metadata={"source_type": memory.source_type.value},
                        recommendation_code="review_memory_source",
                    )
                )
            future_tolerance = now + timedelta(minutes=5)
            if (
                _as_utc(memory.occurred_at) > future_tolerance
                or _as_utc(memory.created_at) > future_tolerance
                or _as_utc(memory.updated_at) > future_tolerance
                or (
                    memory.last_retrieved_at is not None
                    and _as_utc(memory.last_retrieved_at) > future_tolerance
                )
            ):
                issues.append(
                    _issue(
                        code=MemoryDoctorIssueCode.TIMESTAMP_INVALID,
                        severity=MemoryDoctorSeverity.WARNING,
                        subject_type=MemoryDoctorSubjectType.EPISODIC_MEMORY,
                        subject_ids=[memory.memory_id],
                        metadata={"timestamp_state": "future"},
                        recommendation_code="review_memory_timestamp",
                    )
                )
        return issues

    def _active_budget_issues(
        self,
        memories: list[EpisodicMemoryRecord],
        checkpoints: list[PracticeThreadCheckpoint],
        *,
        now: datetime,
    ) -> list[MemoryDoctorIssue]:
        """Audit a worst-case role-play packet through the runtime assembler."""
        allowed_types = set(episodic_types_for_skill("roleplay_skill"))
        active_memories = sorted(
            (
                memory
                for memory in memories
                if memory.status == MemoryRecordStatus.ACTIVE
                and memory.memory_type in allowed_types
            ),
            key=lambda item: (
                item.confidence,
                item.occurred_at,
                item.memory_id,
            ),
            reverse=True,
        )
        active_checkpoints = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.status
            in {PracticeThreadStatus.ACTIVE, PracticeThreadStatus.PAUSED}
        ]
        projected_checkpoints = [
            (checkpoint, projected)
            for checkpoint in active_checkpoints
            if (
                projected := project_checkpoint_context(
                    checkpoint,
                    token_budget=min(
                        256,
                        self.thresholds.active_memory_token_budget,
                    ),
                    estimator=self.token_estimator,
                )
            )
            is not None
        ]
        selected_checkpoint, durable_checkpoint = max(
            projected_checkpoints,
            key=lambda item: (
                item[1].estimated_tokens,
                item[0].updated_at,
                item[0].thread_id,
            ),
            default=(None, None),
        )
        scenario = (
            selected_checkpoint.current_scenario
            if selected_checkpoint is not None
            else next(
                (
                    memory.scenario_type
                    for memory in active_memories
                    if memory.scenario_type is not None
                ),
                None,
            )
        )
        compatible_memories = [
            memory
            for memory in active_memories
            if scenario is None
            or memory.scenario_type is None
            or memory.scenario_type == scenario
        ][:3]
        projection = _doctor_skill_projection(scenario, now=now)
        retrieval = _doctor_retrieval_result(
            compatible_memories,
            estimator=self.token_estimator,
            token_budget=self.thresholds.active_memory_token_budget,
        )
        owner_id = (
            compatible_memories[0].user_id
            if compatible_memories
            else selected_checkpoint.user_id
            if selected_checkpoint is not None
            else "memory-doctor"
        )
        packet = self.active_memory_assembler.assemble(
            user_id=owner_id,
            skill_context=projection,
            current_request=scenario if scenario is not None else "practice",
            durable_checkpoint=durable_checkpoint,
            memory_retrieval=retrieval,
            retrieval_user_id=owner_id,
            consent_allowed=True,
            assembled_at=now,
        )
        budget_drops = [
            selection
            for selection in packet.selections
            if selection.drop_reason == ActiveMemoryDropReason.TOKEN_BUDGET
        ]
        if not budget_drops:
            return []
        attempted_tokens = sum(
            selection.estimated_tokens for selection in packet.selections
        )
        return [
            _issue(
                code=MemoryDoctorIssueCode.ACTIVE_MEMORY_OVER_BUDGET,
                severity=MemoryDoctorSeverity.WARNING,
                subject_type=MemoryDoctorSubjectType.ACTIVE_MEMORY_PACKET,
                subject_ids=[
                    *[memory.memory_id for memory in compatible_memories],
                    *(
                        [selected_checkpoint.thread_id]
                        if selected_checkpoint is not None
                        else []
                    ),
                ],
                metadata={
                    "estimated_tokens": attempted_tokens,
                    "selected_tokens": packet.estimated_tokens,
                    "token_budget": packet.token_budget,
                    "episodic_count": len(compatible_memories),
                    "checkpoint_count": int(selected_checkpoint is not None),
                    "budget_drop_count": len(budget_drops),
                },
                recommendation_code="review_active_memory_budget",
            )
        ]

    def _stale_checkpoint_issues(
        self,
        checkpoints: list[PracticeThreadCheckpoint],
        *,
        now: datetime,
    ) -> list[MemoryDoctorIssue]:
        cutoff = now - timedelta(days=self.thresholds.stale_checkpoint_days)
        return [
            _issue(
                code=MemoryDoctorIssueCode.STALE_CHECKPOINT,
                severity=MemoryDoctorSeverity.INFO,
                subject_type=MemoryDoctorSubjectType.THREAD_CHECKPOINT,
                subject_ids=[checkpoint.thread_id],
                metadata={
                    "status": checkpoint.status.value,
                    "inactive_days": max(
                        0,
                        (now - _as_utc(checkpoint.last_activity_at)).days,
                    ),
                },
                recommendation_code="review_stale_checkpoint",
            )
            for checkpoint in checkpoints
            if checkpoint.status
            in {PracticeThreadStatus.ACTIVE, PracticeThreadStatus.PAUSED}
            and _as_utc(checkpoint.last_activity_at) < cutoff
        ]

    def _aged_proposal_issues(
        self,
        proposals: list[PendingMemoryProposalRecord],
        *,
        now: datetime,
    ) -> list[MemoryDoctorIssue]:
        cutoff = now - timedelta(days=self.thresholds.pending_proposal_days)
        return [
            _issue(
                code=MemoryDoctorIssueCode.PENDING_PROPOSAL_AGED,
                severity=MemoryDoctorSeverity.INFO,
                subject_type=MemoryDoctorSubjectType.MEMORY_PROPOSAL,
                subject_ids=[proposal.proposal_id],
                metadata={
                    "memory_type": proposal.memory_type.value,
                    "pending_days": max(
                        0,
                        (now - _as_utc(proposal.created_at)).days,
                    ),
                    "expired": _as_utc(proposal.expires_at) <= now,
                },
                recommendation_code="review_pending_proposal",
            )
            for proposal in proposals
            if _as_utc(proposal.created_at) < cutoff
        ]

    def _embedding_issues(
        self,
        user_id: str,
    ) -> tuple[MemoryDoctorCheckStatus, list[MemoryDoctorIssue]]:
        if not self.embedding_inspector.enabled:
            return MemoryDoctorCheckStatus.NOT_APPLICABLE, []
        orphan_hashes = [
            _safe_subject_hash(value)
            for value in self.embedding_inspector.orphan_subject_hashes(
                user_id=user_id
            )[:10]
        ]
        if not orphan_hashes:
            return MemoryDoctorCheckStatus.PASSED, []
        return (
            MemoryDoctorCheckStatus.ISSUES_FOUND,
            [
                _issue_from_hashes(
                    code=MemoryDoctorIssueCode.ORPHAN_EMBEDDING,
                    severity=MemoryDoctorSeverity.WARNING,
                    subject_type=MemoryDoctorSubjectType.EMBEDDING_INDEX,
                    subject_hashes=orphan_hashes,
                    metadata={"orphan_count": len(orphan_hashes)},
                    recommendation_code="rebuild_embedding_index",
                )
            ],
        )

    def _checks(
        self,
        issues: list[MemoryDoctorIssue],
        *,
        embedding_check: MemoryDoctorCheckStatus,
    ) -> list[MemoryDoctorCheck]:
        counts: dict[MemoryDoctorIssueCode, int] = defaultdict(int)
        for issue in issues:
            counts[issue.code] += 1
        checks = []
        for code in MemoryDoctorIssueCode:
            if code == MemoryDoctorIssueCode.ORPHAN_EMBEDDING:
                status = embedding_check
                detail = (
                    "embedding_index_disabled"
                    if status == MemoryDoctorCheckStatus.NOT_APPLICABLE
                    else None
                )
            else:
                status = (
                    MemoryDoctorCheckStatus.ISSUES_FOUND
                    if counts[code]
                    else MemoryDoctorCheckStatus.PASSED
                )
                detail = None
            checks.append(
                MemoryDoctorCheck(
                    code=code,
                    status=status,
                    issue_count=counts[code],
                    detail_code=detail,
                )
            )
        return checks


def _doctor_skill_projection(
    scenario: str | None,
    *,
    now: datetime,
) -> SkillContextProjection:
    """Build the smallest runtime-shaped stable layer for budget auditing."""
    if scenario is None:
        return SkillContextProjection(
            skill_name="roleplay_skill",
            selected_at=now,
        )
    return SkillContextProjection(
        skill_name="roleplay_skill",
        values={"scenario": scenario},
        selected_fields=["scenario"],
        field_metadata={
            "scenario": ContextFieldMetadata(
                sources=[ContextValueSource.CURRENT_REQUEST],
                confidence=ContextConfidence.EXPLICIT,
            )
        },
        selected_at=now,
    )


def _doctor_retrieval_result(
    memories: list[EpisodicMemoryRecord],
    *,
    estimator: TokenEstimator,
    token_budget: int,
) -> MemoryRetrievalResult:
    """Adapt loaded records to the assembler without inventing a second budget."""
    hits = [
        MemoryRetrievalHit(
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            summary=memory.summary,
            scenario_type=memory.scenario_type,
            status=memory.status,
            occurred_at=memory.occurred_at,
            score=MemoryRetrievalScore(
                lexical=1.0,
                scenario=1.0 if memory.scenario_type is not None else 0.35,
                recency=1.0,
                novelty=1.0,
                confidence=memory.confidence,
                total=memory.confidence,
            ),
            estimated_tokens=estimator.count(
                f"{memory.memory_type.value}: {memory.summary}"
            ),
        )
        for memory in memories
    ]
    return MemoryRetrievalResult(
        hits=hits,
        diagnostics=MemoryRetrievalDiagnostics(
            strategy=MemoryRetrievalStrategy.METADATA,
            candidate_count=len(hits),
            eligible_count=len(hits),
            returned_count=len(hits),
            estimated_tokens=sum(hit.estimated_tokens for hit in hits),
            token_budget=token_budget,
            abstained=not hits,
            consent_allowed=True,
        ),
    )


def _issue(
    *,
    code: MemoryDoctorIssueCode,
    severity: MemoryDoctorSeverity,
    subject_type: MemoryDoctorSubjectType,
    subject_ids: list[str],
    metadata: dict[str, int | float | str | bool],
    recommendation_code: str,
) -> MemoryDoctorIssue:
    unique_subject_ids = list(dict.fromkeys(subject_ids))
    hashes = [_hash_id(value) for value in unique_subject_ids[:10]]
    return _issue_from_hashes(
        code=code,
        severity=severity,
        subject_type=subject_type,
        subject_hashes=hashes,
        affected_count=len(unique_subject_ids),
        metadata=metadata,
        recommendation_code=recommendation_code,
    )


def _issue_from_hashes(
    *,
    code: MemoryDoctorIssueCode,
    severity: MemoryDoctorSeverity,
    subject_type: MemoryDoctorSubjectType,
    subject_hashes: list[str],
    affected_count: int | None = None,
    metadata: dict[str, int | float | str | bool],
    recommendation_code: str,
) -> MemoryDoctorIssue:
    stable_material = ":".join(
        [code.value, subject_type.value, *sorted(subject_hashes)]
    )
    return MemoryDoctorIssue(
        issue_id=_hash_id(stable_material),
        code=code,
        severity=severity,
        subject_type=subject_type,
        subject_id_hashes=subject_hashes,
        affected_count=max(
            1,
            affected_count
            if affected_count is not None
            else len(subject_hashes),
        ),
        metadata=metadata,
        recommendation_code=recommendation_code,
    )


def _hash_id(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_subject_hash(value: str) -> str:
    normalized = value.casefold()
    if len(normalized) == 16 and all(
        character in "0123456789abcdef" for character in normalized
    ):
        return normalized
    return _hash_id(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("memory doctor timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


memory_doctor_service = MemoryDoctorService()
