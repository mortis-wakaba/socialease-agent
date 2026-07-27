"""Add durable bounded conversation summaries.

Revision ID: 0013_conversation_context
Revises: 0012_unified_conversations
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_conversation_context"
down_revision = "0012_unified_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the rebuildable working-context summary projection."""
    op.create_table(
        "conversation_context_summaries",
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column(
            "compacted_through_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "compacted_through_sequence >= 0",
            name="ck_conversation_context_sequence",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_conversation_context_version",
        ),
    )
    op.create_index(
        "idx_conversation_context_summaries_owner",
        "conversation_context_summaries",
        ["user_id", "conversation_id"],
    )


def downgrade() -> None:
    """Drop the derived conversation summary projection."""
    op.drop_index(
        "idx_conversation_context_summaries_owner",
        table_name="conversation_context_summaries",
    )
    op.drop_table("conversation_context_summaries")
