"""Add confirmation-gated memory proposals.

Revision ID: 0008_add_memory_proposals
Revises: 0007_add_long_term_memory
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_add_memory_proposals"
down_revision = "0007_add_long_term_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create bounded pending proposals without storing rejected content."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    op.add_column(
        "episodic_memories",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    if dialect == "postgresql":
        op.execute(
            """UPDATE episodic_memories
            SET idempotency_key =
                md5(user_id || ':' || memory_id) || md5(memory_id || ':' || user_id)
            WHERE idempotency_key IS NULL"""
        )
        op.alter_column(
            "episodic_memories",
            "idempotency_key",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        _replace_postgres_phase_one_constraints(expanded=True)
    else:
        op.execute(
            """UPDATE episodic_memories
            SET idempotency_key = lower(hex(randomblob(32)))
            WHERE idempotency_key IS NULL"""
        )
        _replace_sqlite_phase_one_constraints(expanded=True)
    op.create_index(
        "idx_episodic_memories_user_idempotency",
        "episodic_memories",
        ["user_id", "idempotency_key"],
        unique=True,
    )

    op.create_table(
        "memory_proposals",
        sa.Column("proposal_id", sa.String(length=128), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("scenario_type", sa.String(length=64), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy_reason", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_proposals_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('pending_confirmation', 'confirmed', 'rejected', 'expired')",
            name="ck_memory_proposals_status",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_memory_proposals_version",
        ),
    )
    op.create_index(
        "idx_memory_proposals_user_status",
        "memory_proposals",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "idx_memory_proposals_user_idempotency",
        "memory_proposals",
        ["user_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    """Drop confirmation-gated memory proposals."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    op.execute(
        """DELETE FROM memory_events
        WHERE subject_type = 'memory_proposal'"""
    )
    op.drop_index(
        "idx_memory_proposals_user_idempotency",
        table_name="memory_proposals",
    )
    op.drop_index(
        "idx_memory_proposals_user_status",
        table_name="memory_proposals",
    )
    op.drop_table("memory_proposals")
    op.execute(
        """UPDATE episodic_memories
        SET memory_type = 'practice_experience'
        WHERE memory_type IN ('social_context', 'recurring_pattern')"""
    )
    op.execute(
        """UPDATE episodic_memories
        SET source_type = 'user_confirmed'
        WHERE source_type = 'chat'"""
    )
    op.drop_index(
        "idx_episodic_memories_user_idempotency",
        table_name="episodic_memories",
    )
    if dialect == "postgresql":
        _replace_postgres_phase_one_constraints(expanded=False)
        op.drop_column("episodic_memories", "idempotency_key")
    else:
        _replace_sqlite_phase_one_constraints(expanded=False)


def _replace_postgres_phase_one_constraints(*, expanded: bool) -> None:
    """Replace checks affected by the Phase 2 enum expansion."""
    op.drop_constraint(
        "ck_episodic_memories_type",
        "episodic_memories",
        type_="check",
    )
    op.drop_constraint(
        "ck_episodic_memories_source_type",
        "episodic_memories",
        type_="check",
    )
    op.drop_constraint(
        "ck_memory_events_subject_type",
        "memory_events",
        type_="check",
    )
    memory_types = (
        "'practice_experience', 'helpful_strategy', 'practice_milestone', "
        "'social_context', 'recurring_pattern'"
        if expanded
        else "'practice_experience', 'helpful_strategy', 'practice_milestone'"
    )
    source_types = (
        "'chat', 'roleplay', 'worksheet', 'exposure', 'session_review', "
        "'user_confirmed'"
        if expanded
        else "'roleplay', 'worksheet', 'exposure', 'session_review', "
        "'user_confirmed'"
    )
    subject_types = (
        "'episodic_memory', 'thread_checkpoint', 'memory_proposal'"
        if expanded
        else "'episodic_memory', 'thread_checkpoint'"
    )
    op.create_check_constraint(
        "ck_episodic_memories_type",
        "episodic_memories",
        f"memory_type IN ({memory_types})",
    )
    op.create_check_constraint(
        "ck_episodic_memories_source_type",
        "episodic_memories",
        f"source_type IN ({source_types})",
    )
    op.create_check_constraint(
        "ck_memory_events_subject_type",
        "memory_events",
        f"subject_type IN ({subject_types})",
    )


def _replace_sqlite_phase_one_constraints(*, expanded: bool) -> None:
    """Use batch recreation for SQLite check and nullability changes."""
    memory_types = (
        "memory_type IN ('practice_experience', 'helpful_strategy', "
        "'practice_milestone', 'social_context', 'recurring_pattern')"
        if expanded
        else "memory_type IN ('practice_experience', 'helpful_strategy', "
        "'practice_milestone')"
    )
    source_types = (
        "source_type IN ('chat', 'roleplay', 'worksheet', 'exposure', "
        "'session_review', 'user_confirmed')"
        if expanded
        else "source_type IN ('roleplay', 'worksheet', 'exposure', "
        "'session_review', 'user_confirmed')"
    )
    subject_types = (
        "subject_type IN ('episodic_memory', 'thread_checkpoint', "
        "'memory_proposal')"
        if expanded
        else "subject_type IN ('episodic_memory', 'thread_checkpoint')"
    )
    with op.batch_alter_table(
        "episodic_memories",
        recreate="always",
    ) as batch:
        batch.drop_constraint("ck_episodic_memories_type", type_="check")
        batch.drop_constraint("ck_episodic_memories_source_type", type_="check")
        batch.create_check_constraint("ck_episodic_memories_type", memory_types)
        batch.create_check_constraint(
            "ck_episodic_memories_source_type",
            source_types,
        )
        if expanded:
            batch.alter_column(
                "idempotency_key",
                existing_type=sa.String(length=64),
                nullable=False,
            )
        else:
            batch.drop_column("idempotency_key")
    with op.batch_alter_table("memory_events", recreate="always") as batch:
        batch.drop_constraint("ck_memory_events_subject_type", type_="check")
        batch.create_check_constraint(
            "ck_memory_events_subject_type",
            subject_types,
        )
