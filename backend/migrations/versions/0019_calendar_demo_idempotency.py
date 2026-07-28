"""Deduplicate protocol-free Calendar actions used by local demos.

Revision ID: 0019_calendar_demo_idempotency
Revises: 0018_calendar_action_outbox
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_calendar_demo_idempotency"
down_revision = "0018_calendar_action_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Prevent unbounded duplicate demo jobs without affecting consented actions."""
    op.create_index(
        "uq_calendar_action_outbox_demo_request",
        "calendar_action_outbox",
        ["user_id", "action_type", "request_hash"],
        unique=True,
        postgresql_where=sa.text("protocol_id IS NULL"),
        sqlite_where=sa.text("protocol_id IS NULL"),
    )


def downgrade() -> None:
    """Remove protocol-free request deduplication."""
    op.drop_index(
        "uq_calendar_action_outbox_demo_request",
        table_name="calendar_action_outbox",
    )
