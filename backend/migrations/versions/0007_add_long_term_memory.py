"""Add durable episodic memory, checkpoints, and audit events.

Revision ID: 0007_add_long_term_memory
Revises: 0006_add_session_reviews
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_add_long_term_memory"
down_revision = "0006_add_session_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create normalized long-term memory tables and scoped indexes."""
    op.create_table(
        "episodic_memories",
        sa.Column("memory_id", sa.String(length=128), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("scenario_type", sa.String(length=64), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_version", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_episodic_memories_confidence",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_episodic_memories_version",
        ),
        sa.CheckConstraint(
            "memory_type IN ('practice_experience', 'helpful_strategy', "
            "'practice_milestone')",
            name="ck_episodic_memories_type",
        ),
        sa.CheckConstraint(
            "source_type IN ('roleplay', 'worksheet', 'exposure', "
            "'session_review', 'user_confirmed')",
            name="ck_episodic_memories_source_type",
        ),
        sa.CheckConstraint(
            "evidence_type IN ('explicit_user_statement', "
            "'completed_product_action', 'user_confirmed')",
            name="ck_episodic_memories_evidence_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'archived', "
            "'superseded', 'revoked')",
            name="ck_episodic_memories_status",
        ),
    )
    op.create_index(
        "idx_episodic_memories_user_status",
        "episodic_memories",
        ["user_id", "status", "occurred_at"],
    )
    op.create_index(
        "idx_episodic_memories_user_hash",
        "episodic_memories",
        ["user_id", "content_hash"],
    )
    op.create_index(
        "idx_episodic_memories_source",
        "episodic_memories",
        ["user_id", "source_type", "source_id"],
    )

    op.create_table(
        "thread_checkpoints",
        sa.Column("thread_id", sa.String(length=128), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("current_goal", sa.String(length=64), nullable=True),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("current_scenario", sa.String(length=64), nullable=True),
        sa.Column("helpful_strategy_codes", sa.JSON(), nullable=False),
        sa.Column("attempted_skill_names", sa.JSON(), nullable=False),
        sa.Column("unresolved_next_step", sa.String(length=240), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_thread_checkpoints_version",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'archived')",
            name="ck_thread_checkpoints_status",
        ),
    )
    op.create_index(
        "idx_thread_checkpoints_user_status",
        "thread_checkpoints",
        ["user_id", "status", "updated_at"],
    )

    op.create_table(
        "memory_events",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("subject_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subject_version >= 1",
            name="ck_memory_events_subject_version",
        ),
        sa.CheckConstraint(
            "subject_type IN ('episodic_memory', 'thread_checkpoint')",
            name="ck_memory_events_subject_type",
        ),
    )
    op.create_index(
        "idx_memory_events_user_created",
        "memory_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "idx_memory_events_subject",
        "memory_events",
        ["user_id", "subject_type", "subject_id", "created_at"],
    )


def downgrade() -> None:
    """Drop durable memory tables in dependency-safe order."""
    op.drop_index("idx_memory_events_subject", table_name="memory_events")
    op.drop_index("idx_memory_events_user_created", table_name="memory_events")
    op.drop_table("memory_events")
    op.drop_index(
        "idx_thread_checkpoints_user_status",
        table_name="thread_checkpoints",
    )
    op.drop_table("thread_checkpoints")
    op.drop_index("idx_episodic_memories_source", table_name="episodic_memories")
    op.drop_index("idx_episodic_memories_user_hash", table_name="episodic_memories")
    op.drop_index(
        "idx_episodic_memories_user_status",
        table_name="episodic_memories",
    )
    op.drop_table("episodic_memories")
