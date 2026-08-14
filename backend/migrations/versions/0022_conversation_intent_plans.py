"""Persist one current sequential intent plan per conversation.

Revision ID: 0022_conversation_intent_plans
Revises: 0021_add_memory_vector_index
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op


revision = "0022_conversation_intent_plans"
down_revision = "0021_add_memory_vector_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create owner-scoped intent plan persistence."""
    op.create_table(
        "conversation_intent_plans",
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')",
            name="ck_conversation_intent_plans_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_conversation_intent_plans_version"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
        sa.UniqueConstraint("plan_id"),
    )
    op.create_index(
        "idx_conversation_intent_plans_owner_status",
        "conversation_intent_plans",
        ["user_id", "status", "updated_at"],
    )


def downgrade() -> None:
    """Drop persistent intent plan state."""
    op.drop_index(
        "idx_conversation_intent_plans_owner_status",
        table_name="conversation_intent_plans",
    )
    op.drop_table("conversation_intent_plans")
