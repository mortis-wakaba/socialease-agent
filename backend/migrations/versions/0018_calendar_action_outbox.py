"""Add the transactional Calendar action outbox.

Revision ID: 0018_calendar_action_outbox
Revises: 0017_module_outbox_leases
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_calendar_action_outbox"
down_revision = "0017_module_outbox_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the durable external-action queue."""
    op.create_table(
        "calendar_action_outbox",
        sa.Column("job_id", sa.String(length=64), primary_key=True),
        sa.Column("protocol_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action_type IN ('create', 'update', 'delete')",
            name="ck_calendar_action_outbox_action",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'dead_letter')",
            name="ck_calendar_action_outbox_status",
        ),
        sa.UniqueConstraint("protocol_id", name="uq_calendar_action_outbox_protocol"),
    )
    op.create_index(
        "idx_calendar_action_outbox_due",
        "calendar_action_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    """Drop the Calendar action queue."""
    op.drop_index(
        "idx_calendar_action_outbox_due",
        table_name="calendar_action_outbox",
    )
    op.drop_table("calendar_action_outbox")
