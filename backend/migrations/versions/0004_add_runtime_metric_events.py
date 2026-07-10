"""Add runtime metric events.

Revision ID: 0004_add_runtime_metric_events
Revises: 0003_add_structured_query_fields
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_add_runtime_metric_events"
down_revision = "0003_add_structured_query_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create aggregate runtime metrics table without identifying fields."""
    op.create_table(
        "harness_runtime_metric_events",
        sa.Column("metric_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_harness_runtime_metric_events_name",
        "harness_runtime_metric_events",
        ["event_name"],
    )
    op.create_index(
        "idx_harness_runtime_metric_events_created_at",
        "harness_runtime_metric_events",
        ["created_at"],
    )


def downgrade() -> None:
    """Drop aggregate runtime metrics table."""
    op.drop_index(
        "idx_harness_runtime_metric_events_created_at",
        table_name="harness_runtime_metric_events",
    )
    op.drop_index(
        "idx_harness_runtime_metric_events_name",
        table_name="harness_runtime_metric_events",
    )
    op.drop_table("harness_runtime_metric_events")
