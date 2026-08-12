"""Add persistent pgvector embeddings for hybrid memory candidate recall.

Revision ID: 0021_add_memory_vector_index
Revises: 0020_add_memory_fts_index
Create Date: 2026-08-08
"""

from alembic import op


revision = "0021_add_memory_vector_index"
down_revision = "0020_add_memory_fts_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install pgvector and add a nullable, versioned HNSW embedding index."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE episodic_memories ADD COLUMN embedding vector(512)"
    )
    op.execute(
        "ALTER TABLE episodic_memories ADD COLUMN embedding_model text"
    )
    op.execute(
        "ALTER TABLE episodic_memories ADD COLUMN embedding_revision text"
    )
    op.execute(
        "ALTER TABLE episodic_memories "
        "ADD COLUMN embedding_updated_at timestamptz"
    )
    op.execute(
        """CREATE INDEX idx_episodic_memories_embedding_hnsw
        ON episodic_memories
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        WHERE embedding IS NOT NULL"""
    )
    op.create_index(
        "idx_episodic_memories_embedding_revision",
        "episodic_memories",
        ["embedding_revision"],
    )


def downgrade() -> None:
    """Remove production vector metadata while retaining the shared extension."""
    op.drop_index(
        "idx_episodic_memories_embedding_revision",
        table_name="episodic_memories",
    )
    op.execute("DROP INDEX IF EXISTS idx_episodic_memories_embedding_hnsw")
    op.drop_column("episodic_memories", "embedding_updated_at")
    op.drop_column("episodic_memories", "embedding_revision")
    op.drop_column("episodic_memories", "embedding_model")
    op.drop_column("episodic_memories", "embedding")
