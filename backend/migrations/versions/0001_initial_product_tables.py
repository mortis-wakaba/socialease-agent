"""Initial product tables for the PostgreSQL production target.

Revision ID: 0001_initial_product_tables
Revises:
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_product_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create first-cut product tables matching current repository payloads."""
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("product_safe", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "roleplay_sessions",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "worksheets",
        sa.Column("worksheet_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "exposure_plans",
        sa.Column("plan_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "exposure_attempts",
        sa.Column("attempt_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plan_id", sa.String(), sa.ForeignKey("exposure_plans.plan_id"), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "protocols",
        sa.Column("protocol_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("protocol_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, index=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("harness_action", sa.String(), nullable=True),
        sa.Column("request_hash", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_protocols_user_status", "protocols", ["user_id", "status"])
    op.create_index("idx_protocols_expiration", "protocols", ["status", "expires_at"])
    op.create_index(
        "idx_protocols_action_hash",
        "protocols",
        ["user_id", "harness_action", "request_hash"],
    )
    op.create_table(
        "intervention_plans",
        sa.Column("plan_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("session_id", sa.String(), nullable=False, index=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_intervention_plans_status",
        "intervention_plans",
        ["user_id", "status", "updated_at"],
    )
    op.create_table(
        "user_memory_settings",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop product tables."""
    op.drop_table("user_memory_settings")
    op.drop_index("idx_intervention_plans_status", table_name="intervention_plans")
    op.drop_table("intervention_plans")
    op.drop_index("idx_protocols_action_hash", table_name="protocols")
    op.drop_index("idx_protocols_expiration", table_name="protocols")
    op.drop_index("idx_protocols_user_status", table_name="protocols")
    op.drop_table("protocols")
    op.drop_table("exposure_attempts")
    op.drop_table("exposure_plans")
    op.drop_table("worksheets")
    op.drop_table("roleplay_sessions")
    op.drop_table("runs")
