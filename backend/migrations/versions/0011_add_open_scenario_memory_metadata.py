"""Add open-scenario metadata to episodic memories and proposals.

Revision ID: 0011_open_scenario_memory
Revises: 0010_open_scenario_checkpoint
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_open_scenario_memory"
down_revision = "0010_open_scenario_checkpoint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add continuity and transferable-skill metadata."""
    for table_name in ("episodic_memories", "memory_proposals"):
        op.add_column(
            table_name,
            sa.Column("scenario_id", sa.String(length=128), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("practice_thread_id", sa.String(length=128), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column(
                "skill_codes",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "context_tags",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
    op.create_index(
        "idx_episodic_memories_continuity",
        "episodic_memories",
        [
            "user_id",
            "status",
            "practice_thread_id",
            "scenario_id",
            "occurred_at",
        ],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE INDEX idx_episodic_memories_skill_codes_gin
            ON episodic_memories USING gin ((skill_codes::jsonb))"""
        )


def downgrade() -> None:
    """Remove open-scenario memory metadata."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_episodic_memories_skill_codes_gin")
    op.drop_index(
        "idx_episodic_memories_continuity",
        table_name="episodic_memories",
    )
    for table_name in ("memory_proposals", "episodic_memories"):
        op.drop_column(table_name, "context_tags")
        op.drop_column(table_name, "skill_codes")
        op.drop_column(table_name, "practice_thread_id")
        op.drop_column(table_name, "scenario_id")
