"""Add durable unified conversation timelines and module state.

Revision ID: 0012_unified_conversations
Revises: 0011_open_scenario_memory
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_unified_conversations"
down_revision = "0011_open_scenario_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create owner-scoped conversation tables and ordering constraints."""
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "active_module_depth",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "history_notice_version",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name="ck_conversations_status",
        ),
        sa.CheckConstraint(
            "active_module_depth BETWEEN 0 AND 3",
            name="ck_conversations_module_depth",
        ),
        sa.CheckConstraint("version >= 1", name="ck_conversations_version"),
    )
    op.create_index(
        "idx_conversations_user_updated",
        "conversations",
        ["user_id", "updated_at", "conversation_id"],
    )
    op.create_table(
        "conversation_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content_plaintext", sa.Text(), nullable=True),
        sa.Column("content_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("content_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("content_key_version", sa.String(length=64), nullable=True),
        sa.Column("structured_payload", sa.JSON(), nullable=True),
        sa.Column("module_run_id", sa.String(length=64), nullable=True),
        sa.Column("parent_module_run_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_conversation_events_sequence",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "idempotency_key",
            name="uq_conversation_events_idempotency",
        ),
        sa.CheckConstraint(
            """(content_plaintext IS NOT NULL
                AND content_ciphertext IS NULL
                AND content_nonce IS NULL
                AND content_key_version IS NULL)
            OR (content_plaintext IS NULL
                AND content_ciphertext IS NOT NULL
                AND content_nonce IS NOT NULL
                AND content_key_version IS NOT NULL)""",
            name="ck_conversation_events_content_storage",
        ),
    )
    op.create_index(
        "idx_conversation_events_owner_sequence",
        "conversation_events",
        ["user_id", "conversation_id", "sequence_no"],
    )
    op.create_table(
        "conversation_module_proposals",
        sa.Column("proposal_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("proposed_module", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'expired')",
            name="ck_conversation_module_proposals_status",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "request_hash",
            name="uq_conversation_module_proposals_request",
        ),
    )
    op.create_index(
        "idx_conversation_module_proposals_owner_status",
        "conversation_module_proposals",
        ["user_id", "conversation_id", "status", "created_at"],
    )
    op.create_table(
        "conversation_module_runs",
        sa.Column("module_run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("module_type", sa.String(length=16), nullable=False),
        sa.Column(
            "parent_module_run_id",
            sa.String(length=64),
            sa.ForeignKey("conversation_module_runs.module_run_id"),
            nullable=True,
        ),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("domain_session_id", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "depth BETWEEN 1 AND 3",
            name="ck_conversation_module_runs_depth",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'completed', 'terminated')",
            name="ck_conversation_module_runs_status",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_conversation_module_runs_version",
        ),
    )
    op.create_index(
        "idx_conversation_module_runs_stack",
        "conversation_module_runs",
        ["user_id", "conversation_id", "depth"],
    )


def downgrade() -> None:
    """Drop unified conversation state in dependency order."""
    op.drop_index(
        "idx_conversation_module_runs_stack",
        table_name="conversation_module_runs",
    )
    op.drop_table("conversation_module_runs")
    op.drop_index(
        "idx_conversation_module_proposals_owner_status",
        table_name="conversation_module_proposals",
    )
    op.drop_table("conversation_module_proposals")
    op.drop_index(
        "idx_conversation_events_owner_sequence",
        table_name="conversation_events",
    )
    op.drop_table("conversation_events")
    op.drop_index("idx_conversations_user_updated", table_name="conversations")
    op.drop_table("conversations")
