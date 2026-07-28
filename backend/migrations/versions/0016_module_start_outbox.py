"""Add the durable module-start outbox.

Revision ID: 0016_module_start_outbox
Revises: 0015_conversation_commands
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_module_start_outbox"
down_revision = "0015_conversation_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create replayable module-start side-effect records."""
    op.create_table(
        "conversation_module_start_outbox",
        sa.Column("module_run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed')",
            name="ck_conversation_module_start_outbox_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_conversation_module_start_outbox_attempt_count",
        ),
    )
    op.create_index(
        "idx_conversation_module_start_outbox_pending",
        "conversation_module_start_outbox",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    """Drop the module-start outbox."""
    op.drop_index(
        "idx_conversation_module_start_outbox_pending",
        table_name="conversation_module_start_outbox",
    )
    op.drop_table("conversation_module_start_outbox")
