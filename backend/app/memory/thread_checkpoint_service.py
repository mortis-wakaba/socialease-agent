"""Durable, consent-gated checkpoint lifecycle for long role-play threads."""

from datetime import datetime, timedelta, timezone
import logging

from app.memory.long_term_repository import (
    LongTermMemoryRepository,
    MemoryConflictError,
)
from app.memory.settings_store import UserMemorySettingsRepository
from app.memory.token_estimator import ConservativeTokenEstimator, TokenEstimator
from app.models_long_term_memory import (
    PracticeThreadCheckpoint,
    PracticeThreadStatus,
)
from app.models_scenario import ScenarioSpec
from app.models_session_context import (
    DurableCheckpointContext,
    RoleplayCompactState,
)
from app.privacy.redaction import redact_sensitive_identifiers


logger = logging.getLogger(__name__)
_RESTORABLE_STATUSES = {
    PracticeThreadStatus.ACTIVE,
    PracticeThreadStatus.PAUSED,
}
_ALLOWED_STATUS_TRANSITIONS = {
    PracticeThreadStatus.ACTIVE: {
        PracticeThreadStatus.ACTIVE,
        PracticeThreadStatus.PAUSED,
        PracticeThreadStatus.COMPLETED,
        PracticeThreadStatus.ARCHIVED,
    },
    PracticeThreadStatus.PAUSED: {
        PracticeThreadStatus.PAUSED,
        PracticeThreadStatus.ACTIVE,
        PracticeThreadStatus.COMPLETED,
        PracticeThreadStatus.ARCHIVED,
    },
    PracticeThreadStatus.COMPLETED: {
        PracticeThreadStatus.COMPLETED,
        PracticeThreadStatus.ARCHIVED,
    },
    PracticeThreadStatus.ARCHIVED: {PracticeThreadStatus.ARCHIVED},
}


class ThreadCheckpointService:
    """Persist minimal progress and expose only bounded active thread memory."""

    def __init__(
        self,
        *,
        repository: LongTermMemoryRepository,
        settings_repository: UserMemorySettingsRepository,
        token_estimator: TokenEstimator | None = None,
        active_memory_token_budget: int = 256,
        restore_ttl: timedelta = timedelta(days=180),
        max_write_attempts: int = 3,
    ) -> None:
        self.repository = repository
        self.settings_repository = settings_repository
        self.token_estimator = token_estimator or ConservativeTokenEstimator()
        self.active_memory_token_budget = min(
            max(active_memory_token_budget, 128),
            1024,
        )
        self.restore_ttl = restore_ttl
        self.max_write_attempts = min(max(max_write_attempts, 1), 5)

    def record_roleplay(
        self,
        *,
        user_id: str,
        thread_id: str,
        scenario: ScenarioSpec,
        current_stage: str,
        status: PracticeThreadStatus,
        reason_code: str,
        helpful_strategy_codes: list[str] | None = None,
        unresolved_next_step: str | None = None,
        changed_at: datetime | None = None,
        touch_if_unchanged: bool = False,
    ) -> PracticeThreadCheckpoint | None:
        """Create or CAS-update one exact thread without breaking the main flow."""
        timestamp = _as_utc(changed_at or datetime.now(timezone.utc))
        for _attempt in range(self.max_write_attempts):
            current = self.repository.get_checkpoint(thread_id, user_id)
            if (
                current is not None
                and status not in _ALLOWED_STATUS_TRANSITIONS[current.status]
            ):
                logger.warning(
                    "Rejected invalid checkpoint transition: %s_to_%s",
                    current.status.value,
                    status.value,
                )
                return None
            candidate = PracticeThreadCheckpoint(
                thread_id=thread_id,
                user_id=user_id,
                current_goal=current.current_goal if current is not None else None,
                current_stage=current_stage,
                current_scenario=None,
                current_scenario_id=scenario.scenario_id,
                current_scenario_summary=scenario.safe_summary,
                scenario_skill_codes=[
                    skill.value for skill in scenario.skill_codes
                ],
                helpful_strategy_codes=_merge_codes(
                    current.helpful_strategy_codes if current is not None else [],
                    helpful_strategy_codes or [],
                    limit=8,
                ),
                attempted_skill_names=_merge_codes(
                    current.attempted_skill_names if current is not None else [],
                    ["roleplay_skill"],
                    limit=12,
                ),
                unresolved_next_step=_safe_next_step(unresolved_next_step),
                status=status,
                version=current.version if current is not None else 1,
                last_activity_at=timestamp,
                created_at=current.created_at if current is not None else timestamp,
                updated_at=timestamp,
            )
            if (
                current is not None
                and not touch_if_unchanged
                and _same_checkpoint_state(current, candidate)
            ):
                return current
            try:
                return self.repository.save_checkpoint(
                    candidate,
                    expected_version=current.version if current is not None else None,
                    reason_code=reason_code,
                    changed_at=timestamp,
                )
            except MemoryConflictError:
                continue
            except Exception as error:
                logger.warning(
                    "Checkpoint persistence failed: %s",
                    error.__class__.__name__,
                )
                return None
        logger.warning("Checkpoint persistence exhausted bounded CAS retries")
        return None

    def restore_roleplay_context(
        self,
        *,
        user_id: str,
        thread_id: str,
        expected_scenario_id: str,
        now: datetime | None = None,
    ) -> DurableCheckpointContext | None:
        """Return active memory only with consent, exact scope, freshness and budget."""
        settings = self.settings_repository.get(user_id)
        if not settings.consent_state.consent_to_practice_summary:
            return None
        checkpoint = self.repository.get_checkpoint(thread_id, user_id)
        if checkpoint is None or checkpoint.status not in _RESTORABLE_STATUSES:
            return None
        if checkpoint.current_scenario_id != expected_scenario_id:
            return None
        timestamp = _as_utc(now or datetime.now(timezone.utc))
        if checkpoint.last_activity_at + self.restore_ttl < timestamp:
            return None
        return self._bounded_context(checkpoint)

    def _bounded_context(
        self,
        checkpoint: PracticeThreadCheckpoint,
    ) -> DurableCheckpointContext | None:
        return project_checkpoint_context(
            checkpoint,
            token_budget=self.active_memory_token_budget,
            estimator=self.token_estimator,
        )


def project_checkpoint_context(
    checkpoint: PracticeThreadCheckpoint,
    *,
    token_budget: int,
    estimator: TokenEstimator,
) -> DurableCheckpointContext | None:
    """Project a stored checkpoint with the same runtime compaction contract."""
    bounded_budget = min(max(token_budget, 128), 1024)
    skills = _merge_codes(
        checkpoint.helpful_strategy_codes,
        checkpoint.attempted_skill_names,
        limit=6,
    )
    state = RoleplayCompactState(
        user_goal=checkpoint.current_goal.value if checkpoint.current_goal else None,
        current_topic=_checkpoint_topic(checkpoint),
        unresolved_question=_safe_next_step(checkpoint.unresolved_next_step),
        practiced_skills=skills,
        compacted_through_message=0,
        source_message_count=0,
        version=checkpoint.version,
        updated_at=checkpoint.updated_at,
    )
    state = _fit_active_memory(
        state,
        token_budget=bounded_budget,
        estimator=estimator,
    )
    estimated = estimator.count(state.model_dump_json())
    if estimated > bounded_budget:
        return None
    return DurableCheckpointContext(
        compact_state=state,
        checkpoint_version=checkpoint.version,
        estimated_tokens=estimated,
        token_budget=bounded_budget,
    )


def _fit_active_memory(
    state: RoleplayCompactState,
    *,
    token_budget: int,
    estimator: TokenEstimator,
) -> RoleplayCompactState:
    """Reduce optional active-memory fields until the independent budget is met."""
    candidate = state.model_copy(deep=True)
    while estimator.count(candidate.model_dump_json()) > token_budget:
        if candidate.practiced_skills:
            candidate.practiced_skills.pop()
            continue
        if candidate.unresolved_question:
            if len(candidate.unresolved_question) > 24:
                candidate.unresolved_question = candidate.unresolved_question[
                    : len(candidate.unresolved_question) // 2
                ]
            else:
                candidate.unresolved_question = None
            continue
        if candidate.user_goal is not None:
            candidate.user_goal = None
            continue
        if candidate.current_topic is not None:
            candidate.current_topic = None
            continue
        break
    return candidate


def _checkpoint_topic(checkpoint: PracticeThreadCheckpoint) -> str | None:
    parts = []
    if checkpoint.current_scenario is not None:
        parts.append(f"scenario:{checkpoint.current_scenario}")
    elif checkpoint.current_scenario_summary is not None:
        parts.append(f"scenario:{checkpoint.current_scenario_summary}")
    if checkpoint.current_stage is not None:
        parts.append(f"stage:{checkpoint.current_stage}")
    return ";".join(parts) or None


def _safe_next_step(value: str | None) -> str | None:
    if value is None:
        return None
    redacted, _ = redact_sensitive_identifiers(" ".join(value.split())[:240])
    return redacted or None


def _merge_codes(existing: list[str], incoming: list[str], *, limit: int) -> list[str]:
    return list(dict.fromkeys([*existing, *incoming]))[:limit]


def _same_checkpoint_state(
    current: PracticeThreadCheckpoint,
    candidate: PracticeThreadCheckpoint,
) -> bool:
    fields = (
        "current_goal",
        "current_stage",
        "current_scenario",
        "current_scenario_id",
        "current_scenario_summary",
        "scenario_skill_codes",
        "helpful_strategy_codes",
        "attempted_skill_names",
        "unresolved_next_step",
        "status",
    )
    return all(getattr(current, field) == getattr(candidate, field) for field in fields)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("checkpoint timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
