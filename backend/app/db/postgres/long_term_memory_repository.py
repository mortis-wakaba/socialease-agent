"""PostgreSQL durable memory repository with transactional audit events."""

from datetime import datetime
import json

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.exc import IntegrityError

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine
from app.memory.long_term_repository import (
    InvalidMemoryTransitionError,
    MemoryConflictError,
    MemoryNotFoundError,
    _TRANSITION_EVENTS,
    _aware_now,
    _memory_event,
    _require_expected_version,
    _require_memory_transition,
    _safe_query_terms,
)
from app.models_long_term_memory import (
    EpisodicMemoryRecord,
    MemoryEvent,
    MemoryEventType,
    MemoryRecordStatus,
    MemorySubjectType,
    MemoryType,
    PracticeThreadCheckpoint,
)


class PostgresLongTermMemoryRepository:
    """PostgreSQL adapter with row locking and user-scoped mutations."""

    def __init__(
        self,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def create_memory(
        self,
        record: EpisodicMemoryRecord,
        *,
        reason_code: str,
    ) -> EpisodicMemoryRecord:
        """Create an active episodic memory and audit event atomically."""
        if record.version != 1 or record.status != MemoryRecordStatus.ACTIVE:
            raise MemoryConflictError("new episodic memory must be active at version 1")
        event = _memory_event(
            record=record,
            event_type=MemoryEventType.MEMORY_COMMITTED,
            from_status=None,
            to_status=record.status,
            reason_code=reason_code,
            created_at=record.created_at,
        )
        try:
            async with self.engine.begin() as connection:
                (await connection.execute(
                    text(
                        """INSERT INTO episodic_memories (
                        memory_id, user_id, memory_type, summary, scenario_type,
                        scenario_id, practice_thread_id, skill_codes, context_tags,
                        source_type, source_id, evidence_type, confidence, status,
                        occurred_at, created_at, updated_at, last_retrieved_at,
                        expires_at, consent_version, content_hash, supersedes_id,
                        version, idempotency_key
                        ) VALUES (
                        :memory_id, :user_id, :memory_type, :summary, :scenario_type,
                        :scenario_id, :practice_thread_id,
                        CAST(:skill_codes AS json), CAST(:context_tags AS json),
                        :source_type, :source_id, :evidence_type, :confidence, :status,
                        :occurred_at, :created_at, :updated_at, :last_retrieved_at,
                        :expires_at, :consent_version, :content_hash, :supersedes_id,
                        :version, :idempotency_key
                        )"""
                    ),
                    _postgres_memory_values(record),
                ))
                await _insert_postgres_event(connection, event)
        except IntegrityError as error:
            raise MemoryConflictError(
                f"episodic memory {record.memory_id!r} already exists"
            ) from error
        return record

    async def get_memory(
        self,
        memory_id: str,
        user_id: str,
    ) -> EpisodicMemoryRecord | None:
        """Return one memory only when both identifier and owner match."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text(
                    """SELECT * FROM episodic_memories
                    WHERE memory_id = :memory_id AND user_id = :user_id"""
                ),
                {"memory_id": memory_id, "user_id": user_id},
            )).mappings().first()
        return _memory_from_mapping(row) if row else None

    async def get_memory_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> EpisodicMemoryRecord | None:
        """Resolve a safely retried write inside the same user scope."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text(
                    """SELECT * FROM episodic_memories
                    WHERE user_id = :user_id
                    AND idempotency_key = :idempotency_key"""
                ),
                {
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                },
            )).mappings().first()
        return _memory_from_mapping(row) if row else None

    async def get_memory_by_content_hash(
        self,
        *,
        user_id: str,
        content_hash: str,
    ) -> EpisodicMemoryRecord | None:
        """Return one exact-content match, preferring a currently usable record."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text(
                    """SELECT * FROM episodic_memories
                    WHERE user_id = :user_id AND content_hash = :content_hash
                    ORDER BY
                        CASE status
                            WHEN 'active' THEN 0
                            WHEN 'inactive' THEN 1
                            WHEN 'archived' THEN 2
                            WHEN 'revoked' THEN 3
                            ELSE 4
                        END,
                        updated_at DESC
                    LIMIT 1"""
                ),
                {"user_id": user_id, "content_hash": content_hash},
            )).mappings().first()
        return _memory_from_mapping(row) if row else None

    async def list_memories(
        self,
        user_id: str,
        *,
        statuses: tuple[MemoryRecordStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[EpisodicMemoryRecord]:
        """Return bounded user-owned memories ordered by occurrence time."""
        bounded_limit = min(max(limit, 1), 500)
        parameters: dict[str, object] = {
            "user_id": user_id,
            "limit": bounded_limit,
        }
        status_clause = ""
        if statuses:
            names: list[str] = []
            for index, status in enumerate(statuses):
                name = f"status_{index}"
                names.append(f":{name}")
                parameters[name] = status.value
            status_clause = f" AND status IN ({', '.join(names)})"
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text(
                    f"""SELECT * FROM episodic_memories
                    WHERE user_id = :user_id{status_clause}
                    ORDER BY occurred_at DESC, created_at DESC
                    LIMIT :limit"""
                ),
                parameters,
            )).mappings().all()
        return [_memory_from_mapping(row) for row in rows]

    async def search_memory_fts_candidates(
        self,
        *,
        user_id: str,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        query_terms: tuple[str, ...],
        now: datetime,
        limit: int = 50,
    ) -> list[EpisodicMemoryRecord]:
        """Use PostgreSQL FTS for ranked, tenant-scoped lexical recall."""
        safe_terms = _safe_query_terms(query_terms)
        if not statuses or not memory_types or not safe_terms:
            return []
        timestamp = _aware_now(now)
        bounded_limit = min(max(limit, 1), 100)
        parameters: dict[str, object] = {
            "user_id": user_id,
            "now": timestamp,
            "limit": bounded_limit,
        }
        status_names: list[str] = []
        for index, status in enumerate(statuses):
            name = f"status_{index}"
            status_names.append(f":{name}")
            parameters[name] = status.value
        type_names: list[str] = []
        for index, memory_type in enumerate(memory_types):
            name = f"memory_type_{index}"
            type_names.append(f":{name}")
            parameters[name] = memory_type.value
        term_queries: list[str] = []
        for index, term in enumerate(safe_terms):
            name = f"fts_term_{index}"
            term_queries.append(f"plainto_tsquery('simple', :{name})")
            parameters[name] = term
        tsquery = " || ".join(term_queries)
        search_vector = (
            "to_tsvector("
            "'simple', socialease_memory_fts_text(summary)"
            ")"
        )
        statement = text(
            f"""SELECT * FROM episodic_memories
            WHERE user_id = :user_id
            AND status IN ({', '.join(status_names)})
            AND memory_type IN ({', '.join(type_names)})
            AND (expires_at IS NULL OR expires_at > :now)
            AND {search_vector} @@ ({tsquery})
            ORDER BY ts_rank_cd({search_vector}, ({tsquery})) DESC,
                occurred_at DESC, memory_id ASC
            LIMIT :limit"""
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                statement,
                parameters,
            )).mappings().all()
        return [_memory_from_mapping(row) for row in rows]

    async def search_memory_candidates(
        self,
        *,
        user_id: str,
        statuses: tuple[MemoryRecordStatus, ...],
        memory_types: tuple[MemoryType, ...],
        scenario_type: str | None,
        require_scenario_match: bool,
        query_terms: tuple[str, ...],
        now: datetime,
        limit: int = 50,
    ) -> list[EpisodicMemoryRecord]:
        """Apply tenant, lifecycle, type, expiry and optional SQL text filters."""
        if not statuses or not memory_types:
            return []
        timestamp = _aware_now(now)
        bounded_limit = min(max(limit, 1), 100)
        parameters: dict[str, object] = {
            "user_id": user_id,
            "now": timestamp,
            "limit": bounded_limit,
        }
        status_names: list[str] = []
        for index, status in enumerate(statuses):
            name = f"status_{index}"
            status_names.append(f":{name}")
            parameters[name] = status.value
        type_names: list[str] = []
        for index, memory_type in enumerate(memory_types):
            name = f"memory_type_{index}"
            type_names.append(f":{name}")
            parameters[name] = memory_type.value
        query = (
            "SELECT * FROM episodic_memories "
            "WHERE user_id = :user_id "
            f"AND status IN ({', '.join(status_names)}) "
            f"AND memory_type IN ({', '.join(type_names)}) "
            "AND (expires_at IS NULL OR expires_at > :now)"
        )
        if require_scenario_match and scenario_type is not None:
            query += (
                " AND (scenario_type = :scenario_type "
                "OR scenario_type IS NULL)"
            )
            parameters["scenario_type"] = scenario_type
        safe_terms = _safe_query_terms(query_terms)
        if safe_terms:
            term_clauses: list[str] = []
            for index, term in enumerate(safe_terms):
                name = f"query_term_{index}"
                term_clauses.append(f"lower(summary) LIKE :{name}")
                parameters[name] = f"%{term}%"
            query += f" AND ({' OR '.join(term_clauses)})"
        query += (
            " ORDER BY occurred_at DESC, "
            "CASE WHEN last_retrieved_at IS NULL THEN 0 ELSE 1 END, "
            "last_retrieved_at ASC LIMIT :limit"
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text(query),
                parameters,
            )).mappings().all()
        return [_memory_from_mapping(row) for row in rows]

    async def record_retrieval(
        self,
        *,
        user_id: str,
        memory_ids: tuple[str, ...],
        retrieved_at: datetime,
        reason_code: str,
    ) -> int:
        """Atomically stamp bounded user-scoped hits and append content-free events."""
        timestamp = _aware_now(retrieved_at)
        unique_ids = tuple(dict.fromkeys(memory_ids))[:3]
        updated_count = 0
        async with self.engine.begin() as connection:
            for memory_id in unique_ids:
                row = (await connection.execute(
                    text(
                        """SELECT * FROM episodic_memories
                        WHERE memory_id = :memory_id AND user_id = :user_id
                        FOR UPDATE"""
                    ),
                    {"memory_id": memory_id, "user_id": user_id},
                )).mappings().first()
                if row is None:
                    continue
                record = _memory_from_mapping(row)
                (await connection.execute(
                    text(
                        """UPDATE episodic_memories
                        SET last_retrieved_at = GREATEST(
                            COALESCE(last_retrieved_at, :retrieved_at),
                            :retrieved_at
                        )
                        WHERE memory_id = :memory_id AND user_id = :user_id"""
                    ),
                    {
                        "retrieved_at": timestamp,
                        "memory_id": memory_id,
                        "user_id": user_id,
                    },
                ))
                await _insert_postgres_event(
                    connection,
                    MemoryEvent(
                        user_id=user_id,
                        subject_type=MemorySubjectType.EPISODIC_MEMORY,
                        subject_id=memory_id,
                        event_type=MemoryEventType.MEMORY_RETRIEVED,
                        from_status=record.status.value,
                        to_status=record.status.value,
                        reason_code=reason_code,
                        subject_version=record.version,
                        created_at=timestamp,
                    ),
                )
                updated_count += 1
        return updated_count

    async def transition_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        target_status: MemoryRecordStatus,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> EpisodicMemoryRecord:
        """Apply one row-locked compare-and-swap lifecycle transition."""
        timestamp = _aware_now(changed_at)
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                text(
                    """SELECT * FROM episodic_memories
                    WHERE memory_id = :memory_id AND user_id = :user_id
                    FOR UPDATE"""
                ),
                {"memory_id": memory_id, "user_id": user_id},
            )).mappings().first()
            if row is None:
                raise MemoryNotFoundError("user-scoped episodic memory was not found")
            current = _memory_from_mapping(row)
            _require_expected_version(current.version, expected_version)
            _require_memory_transition(current.status, target_status)
            updated = current.model_copy(
                update={
                    "status": target_status,
                    "updated_at": timestamp,
                    "version": current.version + 1,
                }
            )
            result = (await connection.execute(
                text(
                    """UPDATE episodic_memories
                    SET status = :status, updated_at = :updated_at, version = :version
                    WHERE memory_id = :memory_id
                    AND user_id = :user_id
                    AND version = :expected_version"""
                ),
                {
                    "status": updated.status.value,
                    "updated_at": updated.updated_at,
                    "version": updated.version,
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "expected_version": expected_version,
                },
            ))
            if result.rowcount != 1:
                raise MemoryConflictError("episodic memory was changed concurrently")
            await _insert_postgres_event(
                connection,
                _memory_event(
                    record=updated,
                    event_type=_TRANSITION_EVENTS[target_status],
                    from_status=current.status,
                    to_status=target_status,
                    reason_code=reason_code,
                    created_at=timestamp,
                ),
            )
        return updated

    async def update_memory_summary(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        summary: str,
        content_hash: str,
        idempotency_key: str,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> EpisodicMemoryRecord:
        """Update a safe summary with row locking and content-free audit."""
        timestamp = _aware_now(changed_at)
        try:
            async with self.engine.begin() as connection:
                row = (await connection.execute(
                    text(
                        """SELECT * FROM episodic_memories
                        WHERE memory_id = :memory_id AND user_id = :user_id
                        FOR UPDATE"""
                    ),
                    {"memory_id": memory_id, "user_id": user_id},
                )).mappings().first()
                if row is None:
                    raise MemoryNotFoundError(
                        "user-scoped episodic memory was not found"
                    )
                current = _memory_from_mapping(row)
                _require_expected_version(current.version, expected_version)
                updated = current.model_copy(
                    update={
                        "summary": summary,
                        "content_hash": content_hash,
                        "idempotency_key": idempotency_key,
                        "updated_at": timestamp,
                        "version": current.version + 1,
                    }
                )
                result = (await connection.execute(
                    text(
                        """UPDATE episodic_memories
                        SET summary = :summary, content_hash = :content_hash,
                            idempotency_key = :idempotency_key,
                            updated_at = :updated_at, version = :version
                        WHERE memory_id = :memory_id
                        AND user_id = :user_id
                        AND version = :expected_version"""
                    ),
                    {
                        "summary": updated.summary,
                        "content_hash": updated.content_hash,
                        "idempotency_key": updated.idempotency_key,
                        "updated_at": updated.updated_at,
                        "version": updated.version,
                        "memory_id": memory_id,
                        "user_id": user_id,
                        "expected_version": expected_version,
                    },
                ))
                if result.rowcount != 1:
                    raise MemoryConflictError(
                        "episodic memory was changed concurrently"
                    )
                await _insert_postgres_event(
                    connection,
                    _memory_event(
                        record=updated,
                        event_type=MemoryEventType.MEMORY_UPDATED,
                        from_status=current.status,
                        to_status=current.status,
                        reason_code=reason_code,
                        created_at=timestamp,
                    ),
                )
        except IntegrityError as error:
            raise MemoryConflictError(
                "an equivalent episodic memory already exists"
            ) from error
        return updated

    async def delete_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        expected_version: int,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> None:
        """Physically delete memory content while retaining a content-free event."""
        timestamp = _aware_now(changed_at)
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                text(
                    """SELECT * FROM episodic_memories
                    WHERE memory_id = :memory_id AND user_id = :user_id
                    FOR UPDATE"""
                ),
                {"memory_id": memory_id, "user_id": user_id},
            )).mappings().first()
            if row is None:
                raise MemoryNotFoundError("user-scoped episodic memory was not found")
            current = _memory_from_mapping(row)
            _require_expected_version(current.version, expected_version)
            event = _memory_event(
                record=current.model_copy(update={"version": current.version + 1}),
                event_type=MemoryEventType.MEMORY_DELETED,
                from_status=current.status,
                to_status=None,
                reason_code=reason_code,
                created_at=timestamp,
            )
            result = (await connection.execute(
                text(
                    """DELETE FROM episodic_memories
                    WHERE memory_id = :memory_id
                    AND user_id = :user_id
                    AND version = :expected_version"""
                ),
                {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "expected_version": expected_version,
                },
            ))
            if result.rowcount != 1:
                raise MemoryConflictError("episodic memory was changed concurrently")
            await _insert_postgres_event(connection, event)

    async def save_checkpoint(
        self,
        checkpoint: PracticeThreadCheckpoint,
        *,
        expected_version: int | None,
        reason_code: str,
        changed_at: datetime | None = None,
    ) -> PracticeThreadCheckpoint:
        """Create or row-lock and update a user-scoped checkpoint."""
        timestamp = _aware_now(changed_at or checkpoint.updated_at)
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                text(
                    """SELECT * FROM thread_checkpoints
                    WHERE thread_id = :thread_id AND user_id = :user_id
                    FOR UPDATE"""
                ),
                {"thread_id": checkpoint.thread_id, "user_id": checkpoint.user_id},
            )).mappings().first()
            current = _checkpoint_from_mapping(row) if row else None
            if current is None:
                if expected_version is not None or checkpoint.version != 1:
                    raise MemoryConflictError(
                        "new checkpoint requires expected_version=None and version=1"
                    )
                saved = checkpoint
                try:
                    (await connection.execute(
                        text(
                            """INSERT INTO thread_checkpoints (
                            thread_id, user_id, current_goal, current_stage,
                            current_scenario, current_scenario_id,
                            current_scenario_summary, scenario_skill_codes,
                            helpful_strategy_codes,
                            attempted_skill_names, unresolved_next_step, status,
                            version, last_activity_at, created_at, updated_at
                            ) VALUES (
                            :thread_id, :user_id, :current_goal, :current_stage,
                            :current_scenario, :current_scenario_id,
                            :current_scenario_summary,
                            CAST(:scenario_skill_codes AS json),
                            CAST(:helpful_strategy_codes AS json),
                            CAST(:attempted_skill_names AS json),
                            :unresolved_next_step, :status, :version,
                            :last_activity_at, :created_at, :updated_at
                            )"""
                        ),
                        _postgres_checkpoint_values(saved),
                    ))
                except IntegrityError as error:
                    raise MemoryConflictError(
                        f"checkpoint {checkpoint.thread_id!r} already exists"
                    ) from error
                from_status = None
            else:
                if expected_version is None:
                    raise MemoryConflictError("existing checkpoint requires a version")
                _require_expected_version(current.version, expected_version)
                saved = checkpoint.model_copy(
                    update={
                        "created_at": current.created_at,
                        "updated_at": timestamp,
                        "version": current.version + 1,
                    }
                )
                values = _postgres_checkpoint_values(saved)
                values["expected_version"] = expected_version
                result = (await connection.execute(
                    text(
                        """UPDATE thread_checkpoints SET
                        current_goal = :current_goal,
                        current_stage = :current_stage,
                        current_scenario = :current_scenario,
                        current_scenario_id = :current_scenario_id,
                        current_scenario_summary = :current_scenario_summary,
                        scenario_skill_codes =
                            CAST(:scenario_skill_codes AS json),
                        helpful_strategy_codes =
                            CAST(:helpful_strategy_codes AS json),
                        attempted_skill_names =
                            CAST(:attempted_skill_names AS json),
                        unresolved_next_step = :unresolved_next_step,
                        status = :status,
                        version = :version,
                        last_activity_at = :last_activity_at,
                        updated_at = :updated_at
                        WHERE thread_id = :thread_id
                        AND user_id = :user_id
                        AND version = :expected_version"""
                    ),
                    values,
                ))
                if result.rowcount != 1:
                    raise MemoryConflictError("checkpoint was changed concurrently")
                from_status = current.status.value
            await _insert_postgres_event(
                connection,
                MemoryEvent(
                    user_id=saved.user_id,
                    subject_type=MemorySubjectType.THREAD_CHECKPOINT,
                    subject_id=saved.thread_id,
                    event_type=MemoryEventType.CHECKPOINT_UPDATED,
                    from_status=from_status,
                    to_status=saved.status.value,
                    reason_code=reason_code,
                    subject_version=saved.version,
                    created_at=timestamp,
                ),
            )
        return saved

    async def get_checkpoint(
        self,
        thread_id: str,
        user_id: str,
    ) -> PracticeThreadCheckpoint | None:
        """Return a checkpoint only when both thread and owner match."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text(
                    """SELECT * FROM thread_checkpoints
                    WHERE thread_id = :thread_id AND user_id = :user_id"""
                ),
                {"thread_id": thread_id, "user_id": user_id},
            )).mappings().first()
        return _checkpoint_from_mapping(row) if row else None

    async def list_checkpoints(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> list[PracticeThreadCheckpoint]:
        """Return bounded user-owned checkpoints ordered by latest activity."""
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text(
                    """SELECT * FROM thread_checkpoints
                    WHERE user_id = :user_id
                    ORDER BY last_activity_at DESC, thread_id ASC
                    LIMIT :limit"""
                ),
                {
                    "user_id": user_id,
                    "limit": min(max(limit, 1), 500),
                },
            )).mappings().all()
        return [_checkpoint_from_mapping(row) for row in rows]

    async def list_events(
        self,
        *,
        user_id: str,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryEvent]:
        """Return content-free audit events within one user scope."""
        bounded_limit = min(max(limit, 1), 500)
        subject_clause = ""
        parameters: dict[str, object] = {
            "user_id": user_id,
            "limit": bounded_limit,
        }
        if subject_id is not None:
            subject_clause = " AND subject_id = :subject_id"
            parameters["subject_id"] = subject_id
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text(
                    f"""SELECT * FROM memory_events
                    WHERE user_id = :user_id{subject_clause}
                    ORDER BY created_at ASC, event_id ASC
                    LIMIT :limit"""
                ),
                parameters,
            )).mappings().all()
        return [_event_from_mapping(row) for row in rows]


def _postgres_memory_values(record: EpisodicMemoryRecord) -> dict[str, object]:
    return {
        "memory_id": record.memory_id,
        "user_id": record.user_id,
        "memory_type": record.memory_type.value,
        "summary": record.summary,
        "scenario_type": record.scenario_type,
        "scenario_id": record.scenario_id,
        "practice_thread_id": record.practice_thread_id,
        "skill_codes": json.dumps([skill.value for skill in record.skill_codes]),
        "context_tags": json.dumps(record.context_tags),
        "source_type": record.source_type.value,
        "source_id": record.source_id,
        "evidence_type": record.evidence_type.value,
        "confidence": record.confidence,
        "status": record.status.value,
        "occurred_at": record.occurred_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_retrieved_at": record.last_retrieved_at,
        "expires_at": record.expires_at,
        "consent_version": record.consent_version,
        "content_hash": record.content_hash,
        "idempotency_key": record.idempotency_key,
        "supersedes_id": record.supersedes_id,
        "version": record.version,
    }


def _postgres_checkpoint_values(
    checkpoint: PracticeThreadCheckpoint,
) -> dict[str, object]:
    return {
        "thread_id": checkpoint.thread_id,
        "user_id": checkpoint.user_id,
        "current_goal": (
            checkpoint.current_goal.value if checkpoint.current_goal else None
        ),
        "current_stage": checkpoint.current_stage,
        "current_scenario": checkpoint.current_scenario,
        "current_scenario_id": checkpoint.current_scenario_id,
        "current_scenario_summary": checkpoint.current_scenario_summary,
        "scenario_skill_codes": json.dumps(checkpoint.scenario_skill_codes),
        "helpful_strategy_codes": json.dumps(checkpoint.helpful_strategy_codes),
        "attempted_skill_names": json.dumps(checkpoint.attempted_skill_names),
        "unresolved_next_step": checkpoint.unresolved_next_step,
        "status": checkpoint.status.value,
        "version": checkpoint.version,
        "last_activity_at": checkpoint.last_activity_at,
        "created_at": checkpoint.created_at,
        "updated_at": checkpoint.updated_at,
    }


async def _insert_postgres_event(
    connection: AsyncConnection,
    event: MemoryEvent,
) -> None:
    (await connection.execute(
        text(
            """INSERT INTO memory_events (
            event_id, user_id, subject_type, subject_id, event_type,
            from_status, to_status, reason_code, subject_version, created_at
            ) VALUES (
            :event_id, :user_id, :subject_type, :subject_id, :event_type,
            :from_status, :to_status, :reason_code, :subject_version, :created_at
            )"""
        ),
        {
            "event_id": event.event_id,
            "user_id": event.user_id,
            "subject_type": event.subject_type.value,
            "subject_id": event.subject_id,
            "event_type": event.event_type.value,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "reason_code": event.reason_code,
            "subject_version": event.subject_version,
            "created_at": event.created_at,
        },
    ))


def _memory_from_mapping(row: RowMapping) -> EpisodicMemoryRecord:
    return EpisodicMemoryRecord.model_validate(dict(row))


def _checkpoint_from_mapping(row: RowMapping) -> PracticeThreadCheckpoint:
    return PracticeThreadCheckpoint.model_validate(dict(row))


def _event_from_mapping(row: RowMapping) -> MemoryEvent:
    return MemoryEvent.model_validate(dict(row))


__all__ = [
    "InvalidMemoryTransitionError",
    "PostgresLongTermMemoryRepository",
]
