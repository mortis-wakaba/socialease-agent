"""Add the durable conversation command inbox.

Revision ID: 0015_conversation_commands
Revises: 0014_conversation_deletion
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_conversation_commands"
down_revision = "0014_conversation_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create owner-scoped command claims with encrypted-capable results."""
    op.create_table(
        "conversation_commands",
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=200),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_plaintext", sa.Text(), nullable=True),
        sa.Column("result_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("result_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("result_key_version", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing', 'completed')",
            name="ck_conversation_commands_status",
        ),
        sa.CheckConstraint(
            """
            (status = 'processing' AND result_plaintext IS NULL
                AND result_ciphertext IS NULL AND result_nonce IS NULL
                AND result_key_version IS NULL AND completed_at IS NULL)
            OR
            (status = 'completed' AND completed_at IS NOT NULL AND (
                (result_plaintext IS NOT NULL AND result_ciphertext IS NULL
                    AND result_nonce IS NULL AND result_key_version IS NULL)
                OR
                (result_plaintext IS NULL AND result_ciphertext IS NOT NULL
                    AND result_nonce IS NOT NULL
                    AND result_key_version IS NOT NULL)
            ))
            """,
            name="ck_conversation_commands_result_storage",
        ),
    )
    op.create_index(
        "idx_conversation_commands_owner",
        "conversation_commands",
        ["user_id", "conversation_id", "created_at"],
    )


def downgrade() -> None:
    """Drop the conversation command inbox."""
    op.drop_index(
        "idx_conversation_commands_owner",
        table_name="conversation_commands",
    )
    op.drop_table("conversation_commands")
