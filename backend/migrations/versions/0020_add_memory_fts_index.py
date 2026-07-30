"""Add a PostgreSQL full-text index for episodic-memory retrieval.

Revision ID: 0020_add_memory_fts_index
Revises: 0019_calendar_demo_idempotency
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_add_memory_fts_index"
down_revision = "0019_calendar_demo_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index CJK bigrams and ASCII terms without changing the write schema."""
    op.execute(
        """
        CREATE FUNCTION socialease_memory_fts_text(input_text text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            WITH cjk_tokens AS (
                SELECT
                    substr(coalesce(input_text, ''), position, 2) AS token,
                    position AS token_order
                FROM generate_series(
                    1,
                    greatest(char_length(coalesce(input_text, '')) - 1, 0)
                ) AS position
                WHERE substr(coalesce(input_text, ''), position, 2)
                    ~ '^[一-龥]{2}$'
            ),
            ascii_tokens AS (
                SELECT
                    lower(matches.match[1]) AS token,
                    100000 + matches.position AS token_order
                FROM regexp_matches(
                    coalesce(input_text, ''),
                    '([A-Za-z0-9]{2,48})',
                    'g'
                ) WITH ORDINALITY AS matches(match, position)
            )
            SELECT coalesce(
                string_agg(token, ' ' ORDER BY token_order, token),
                ''
            )
            FROM (
                SELECT token, token_order FROM cjk_tokens
                UNION ALL
                SELECT token, token_order FROM ascii_tokens
            ) AS tokens
        $$
        """
    )
    op.create_index(
        "idx_episodic_memories_search_vector",
        "episodic_memories",
        [
            sa.text(
                "to_tsvector("
                "'simple', socialease_memory_fts_text(summary)"
                ")"
            )
        ],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Remove the PostgreSQL FTS expression index."""
    op.drop_index(
        "idx_episodic_memories_search_vector",
        table_name="episodic_memories",
    )
    op.execute("DROP FUNCTION IF EXISTS socialease_memory_fts_text(text)")
