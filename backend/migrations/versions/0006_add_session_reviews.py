"""Add structured session reviews.

Revision ID: 0006_add_session_reviews
Revises: 0005_add_account_tables
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_add_session_reviews"
down_revision = "0005_add_account_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create session review table for low-sensitivity practice summaries."""
    op.create_table(
        "session_reviews",
        sa.Column("review_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_session_reviews_user_id", "session_reviews", ["user_id"])
    op.create_index(
        "idx_session_reviews_user_created",
        "session_reviews",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    """Drop session review table."""
    op.drop_index("idx_session_reviews_user_created", table_name="session_reviews")
    op.drop_index("idx_session_reviews_user_id", table_name="session_reviews")
    op.drop_table("session_reviews")
