"""Add leases, retries and dead-letter state to module reconciliation.

Revision ID: 0017_module_outbox_leases
Revises: 0016_module_start_outbox
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_module_outbox_leases"
down_revision = "0016_module_start_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add production worker coordination fields."""
    op.add_column(
        "conversation_module_start_outbox",
        sa.Column("proposal_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conversation_module_start_outbox",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_module_start_outbox",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "conversation_module_start_outbox",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_module_start_outbox",
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="8",
        ),
    )
    op.add_column(
        "conversation_module_start_outbox",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """UPDATE conversation_module_start_outbox
        SET next_attempt_at = updated_at"""
    )
    with op.batch_alter_table("conversation_module_start_outbox") as batch:
        batch.alter_column("next_attempt_at", nullable=False)
        batch.drop_constraint(
            "ck_conversation_module_start_outbox_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_conversation_module_start_outbox_status",
            "status IN ('pending', 'processing', 'completed', 'dead_letter')",
        )


def downgrade() -> None:
    """Remove worker coordination fields."""
    with op.batch_alter_table("conversation_module_start_outbox") as batch:
        batch.drop_constraint(
            "ck_conversation_module_start_outbox_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_conversation_module_start_outbox_status",
            "status IN ('pending', 'processing', 'completed')",
        )
    for column in (
        "completed_at",
        "max_attempts",
        "lease_expires_at",
        "lease_owner",
        "next_attempt_at",
        "proposal_id",
    ):
        op.drop_column("conversation_module_start_outbox", column)
