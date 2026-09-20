"""Track crisis safety recovery independently from command execution.

Revision ID: 0026_command_safety_recovery
Revises: 0025_command_abandonment
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op


revision = "0026_command_safety_recovery"
down_revision = "0025_command_abandonment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist whether an unfinished command still owes crisis recovery."""
    op.add_column(
        "conversation_commands",
        sa.Column(
            "safety_recovery_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_required",
        ),
    )
    op.create_check_constraint(
        "ck_conversation_commands_safety_recovery",
        "conversation_commands",
        "safety_recovery_status IN ('classification_pending', 'not_required', "
        "'recovery_pending', 'completed')",
    )
    op.alter_column(
        "conversation_commands",
        "safety_recovery_status",
        server_default=None,
    )


def downgrade() -> None:
    """Remove the independently persisted crisis-recovery state."""
    op.drop_constraint(
        "ck_conversation_commands_safety_recovery",
        "conversation_commands",
        type_="check",
    )
    op.drop_column("conversation_commands", "safety_recovery_status")
