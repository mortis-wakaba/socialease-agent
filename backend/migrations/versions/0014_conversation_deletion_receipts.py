"""Add content-free idempotent conversation deletion receipts.

Revision ID: 0014_conversation_deletion
Revises: 0013_conversation_context
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_conversation_deletion"
down_revision = "0013_conversation_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create owner-scoped deletion receipts without conversation content."""
    op.create_table(
        "conversation_deletion_receipts",
        sa.Column("conversation_id", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=128), primary_key=True),
        sa.Column("deleted_counts", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_conversation_deletion_receipts_owner",
        "conversation_deletion_receipts",
        ["user_id", "deleted_at"],
    )


def downgrade() -> None:
    """Drop content-free deletion receipts."""
    op.drop_index(
        "idx_conversation_deletion_receipts_owner",
        table_name="conversation_deletion_receipts",
    )
    op.drop_table("conversation_deletion_receipts")
