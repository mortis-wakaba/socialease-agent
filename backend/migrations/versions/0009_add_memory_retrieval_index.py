"""Add the metadata retrieval index for episodic memory.

Revision ID: 0009_add_memory_retrieval_index
Revises: 0008_add_memory_proposals
Create Date: 2026-07-26
"""

from alembic import op


revision = "0009_add_memory_retrieval_index"
down_revision = "0008_add_memory_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add one tenant-first index used by bounded memory retrieval."""
    op.create_index(
        "idx_episodic_memories_retrieval",
        "episodic_memories",
        [
            "user_id",
            "status",
            "memory_type",
            "scenario_type",
            "occurred_at",
        ],
    )


def downgrade() -> None:
    """Remove the metadata retrieval index."""
    op.drop_index(
        "idx_episodic_memories_retrieval",
        table_name="episodic_memories",
    )
