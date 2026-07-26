"""Add open-scenario metadata to durable practice checkpoints.

Revision ID: 0010_add_open_scenario_checkpoint_metadata
Revises: 0009_add_memory_retrieval_index
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_add_open_scenario_checkpoint_metadata"
down_revision = "0009_add_memory_retrieval_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add privacy-minimized scenario continuity fields."""
    op.add_column(
        "thread_checkpoints",
        sa.Column("current_scenario_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "thread_checkpoints",
        sa.Column("current_scenario_summary", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "thread_checkpoints",
        sa.Column(
            "scenario_skill_codes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_index(
        "idx_thread_checkpoints_user_scenario",
        "thread_checkpoints",
        ["user_id", "current_scenario_id", "updated_at"],
    )


def downgrade() -> None:
    """Remove open-scenario checkpoint metadata."""
    op.drop_index(
        "idx_thread_checkpoints_user_scenario",
        table_name="thread_checkpoints",
    )
    op.drop_column("thread_checkpoints", "scenario_skill_codes")
    op.drop_column("thread_checkpoints", "current_scenario_summary")
    op.drop_column("thread_checkpoints", "current_scenario_id")

