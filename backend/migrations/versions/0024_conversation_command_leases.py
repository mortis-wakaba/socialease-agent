"""Add recoverable leases to conversation commands.

Revision ID: 0024_conversation_command_leases
Revises: 0023_intent_effect_ledger
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op


revision = "0024_conversation_command_leases"
down_revision = "0023_intent_effect_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow abandoned processing commands to be safely reclaimed."""
    op.add_column(
        "conversation_commands",
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conversation_commands",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_commands",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """UPDATE conversation_commands
        SET attempt_count = 1,
            lease_owner = CASE
                WHEN status = 'processing' THEN 'migration-expired'
                ELSE NULL
            END,
            lease_expires_at = CASE
                WHEN status = 'processing' THEN created_at
                ELSE NULL
            END"""
    )
    op.create_check_constraint(
        "ck_conversation_commands_lease",
        "conversation_commands",
        "(status = 'processing' AND lease_owner IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND attempt_count >= 1) OR "
        "(status = 'completed' AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND attempt_count >= 1)",
    )
    op.create_index(
        "idx_conversation_commands_reclaim",
        "conversation_commands",
        ["status", "lease_expires_at"],
    )
    op.alter_column(
        "conversation_commands",
        "attempt_count",
        server_default=None,
    )


def downgrade() -> None:
    """Remove conversation command lease metadata."""
    op.drop_index(
        "idx_conversation_commands_reclaim",
        table_name="conversation_commands",
    )
    op.drop_constraint(
        "ck_conversation_commands_lease",
        "conversation_commands",
        type_="check",
    )
    op.drop_column("conversation_commands", "attempt_count")
    op.drop_column("conversation_commands", "lease_expires_at")
    op.drop_column("conversation_commands", "lease_owner")
