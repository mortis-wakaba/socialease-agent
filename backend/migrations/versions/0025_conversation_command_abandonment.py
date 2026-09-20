"""Add terminal abandonment to conversation commands.

Revision ID: 0025_command_abandonment
Revises: 0024_conversation_command_leases
Create Date: 2026-09-13
"""

import sqlalchemy as sa
from alembic import op


revision = "0025_command_abandonment"
down_revision = "0024_conversation_command_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow expired commands to be terminally abandoned without deletion."""
    op.add_column(
        "conversation_commands",
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "ck_conversation_commands_status",
        "conversation_commands",
        type_="check",
    )
    op.drop_constraint(
        "ck_conversation_commands_result_storage",
        "conversation_commands",
        type_="check",
    )
    op.drop_constraint(
        "ck_conversation_commands_lease",
        "conversation_commands",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conversation_commands_status",
        "conversation_commands",
        "status IN ('processing', 'completed', 'abandoned')",
    )
    op.create_check_constraint(
        "ck_conversation_commands_result_storage",
        "conversation_commands",
        """(status = 'processing' AND result_plaintext IS NULL
                AND result_ciphertext IS NULL AND result_nonce IS NULL
                AND result_key_version IS NULL AND completed_at IS NULL
                AND abandoned_at IS NULL)
            OR
            (status = 'completed' AND completed_at IS NOT NULL
                AND abandoned_at IS NULL AND (
                (result_plaintext IS NOT NULL AND result_ciphertext IS NULL
                    AND result_nonce IS NULL AND result_key_version IS NULL)
                OR
                (result_plaintext IS NULL AND result_ciphertext IS NOT NULL
                    AND result_nonce IS NOT NULL
                    AND result_key_version IS NOT NULL)))
            OR
            (status = 'abandoned' AND result_plaintext IS NULL
                AND result_ciphertext IS NULL AND result_nonce IS NULL
                AND result_key_version IS NULL AND completed_at IS NULL
                AND abandoned_at IS NOT NULL)""",
    )
    op.create_check_constraint(
        "ck_conversation_commands_lease",
        "conversation_commands",
        "(status = 'processing' AND lease_owner IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND attempt_count >= 1) OR "
        "(status IN ('completed', 'abandoned') AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND attempt_count >= 1)",
    )


def downgrade() -> None:
    """Remove abandoned commands before restoring the older lifecycle."""
    op.execute("DELETE FROM conversation_commands WHERE status = 'abandoned'")
    for constraint in (
        "ck_conversation_commands_lease",
        "ck_conversation_commands_result_storage",
        "ck_conversation_commands_status",
    ):
        op.drop_constraint(constraint, "conversation_commands", type_="check")
    op.create_check_constraint(
        "ck_conversation_commands_status",
        "conversation_commands",
        "status IN ('processing', 'completed')",
    )
    op.create_check_constraint(
        "ck_conversation_commands_result_storage",
        "conversation_commands",
        """(status = 'processing' AND result_plaintext IS NULL
                AND result_ciphertext IS NULL AND result_nonce IS NULL
                AND result_key_version IS NULL AND completed_at IS NULL)
            OR
            (status = 'completed' AND completed_at IS NOT NULL AND (
                (result_plaintext IS NOT NULL AND result_ciphertext IS NULL
                    AND result_nonce IS NULL AND result_key_version IS NULL)
                OR
                (result_plaintext IS NULL AND result_ciphertext IS NOT NULL
                    AND result_nonce IS NOT NULL
                    AND result_key_version IS NOT NULL)))""",
    )
    op.create_check_constraint(
        "ck_conversation_commands_lease",
        "conversation_commands",
        "(status = 'processing' AND lease_owner IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND attempt_count >= 1) OR "
        "(status = 'completed' AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND attempt_count >= 1)",
    )
    op.drop_column("conversation_commands", "abandoned_at")
