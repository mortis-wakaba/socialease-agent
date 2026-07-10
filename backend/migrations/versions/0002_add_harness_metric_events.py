"""Add aggregate harness metric events.

Revision ID: 0002_add_harness_metric_events
Revises: 0001_initial_product_tables
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_add_harness_metric_events"
down_revision = "0001_initial_product_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create aggregate metrics table without identifying fields."""
    op.create_table(
        "harness_metric_events",
        sa.Column("metric_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("intent", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=False),
        sa.Column("selected_agent", sa.String(), nullable=False),
        sa.Column("permission_action", sa.String(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("is_crisis", sa.Boolean(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("hook_blocked", sa.Boolean(), nullable=False),
        sa.Column("memory_write_blocked", sa.Boolean(), nullable=False),
        sa.Column("product_boundary_eval", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_harness_metric_events_created_at",
        "harness_metric_events",
        ["created_at"],
    )
    op.create_index(
        "idx_harness_metric_events_risk",
        "harness_metric_events",
        ["risk_level"],
    )
    op.create_index(
        "idx_harness_metric_events_permission",
        "harness_metric_events",
        ["permission_action"],
    )


def downgrade() -> None:
    """Drop aggregate metrics table."""
    op.drop_index("idx_harness_metric_events_permission", table_name="harness_metric_events")
    op.drop_index("idx_harness_metric_events_risk", table_name="harness_metric_events")
    op.drop_index("idx_harness_metric_events_created_at", table_name="harness_metric_events")
    op.drop_table("harness_metric_events")
