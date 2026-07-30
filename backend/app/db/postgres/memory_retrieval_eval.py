"""PostgreSQL-only bulk fixture adapter for isolated memory retrieval evals."""

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.postgres.long_term_memory_repository import _postgres_memory_values
from app.models_long_term_memory import EpisodicMemoryRecord


class PostgresMemoryRetrievalEvalAdapter:
    """Load and remove namespaced demo records in a disposable eval database."""

    _USER_PREFIX = "memory_eval_"

    def __init__(self, *, engine: AsyncEngine) -> None:
        self.engine = engine

    async def replace_records(
        self,
        records: Sequence[EpisodicMemoryRecord],
    ) -> None:
        """Replace only this evaluator's namespaced rows in one transaction."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """DELETE FROM episodic_memories
                    WHERE left(user_id, :prefix_length) = :user_prefix"""
                ),
                {
                    "prefix_length": len(self._USER_PREFIX),
                    "user_prefix": self._USER_PREFIX,
                },
            )
            if records:
                await connection.execute(
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
                    [_postgres_memory_values(record) for record in records],
                )

    async def clear(self) -> None:
        """Remove only namespaced demo records created by this evaluator."""
        await self.replace_records(())
