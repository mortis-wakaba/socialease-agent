"""Add account and session tables for production auth.

Revision ID: 0005_add_account_tables
Revises: 0004_add_runtime_metric_events
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_add_account_tables"
down_revision = "0004_add_runtime_metric_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create account identity and revocable session tables."""
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_users_email", "users", ["email"])
    op.create_table(
        "user_sessions",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("access_token_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("idx_user_sessions_access_token", "user_sessions", ["access_token_id"])
    op.create_index(
        "idx_user_sessions_refresh_token",
        "user_sessions",
        ["refresh_token_hash"],
    )


def downgrade() -> None:
    """Drop account identity and session tables."""
    op.drop_index("idx_user_sessions_refresh_token", table_name="user_sessions")
    op.drop_index("idx_user_sessions_access_token", table_name="user_sessions")
    op.drop_index("idx_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
