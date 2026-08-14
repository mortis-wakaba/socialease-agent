"""Add an independent reversible intent-effect ledger.

Revision ID: 0023_intent_effect_ledger
Revises: 0022_conversation_intent_plans
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op


revision = "0023_intent_effect_ledger"
down_revision = "0022_conversation_intent_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist reversible effects beyond the lifetime of one current plan."""
    op.create_table(
        "conversation_intent_effects",
        sa.Column("effect_id", sa.String(length=64), primary_key=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind = 'calendar_event'", name="ck_intent_effect_kind"),
        sa.CheckConstraint(
            "status IN ('apply_pending', 'applied', 'undo_pending', 'undone')",
            name="ck_intent_effect_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("plan_id", "step_id", name="uq_intent_effect_plan_step"),
    )
    op.create_index(
        "idx_intent_effect_owner_status",
        "conversation_intent_effects",
        ["user_id", "conversation_id", "status", "updated_at"],
    )


def downgrade() -> None:
    """Drop the independent reversible effect ledger."""
    op.drop_index(
        "idx_intent_effect_owner_status",
        table_name="conversation_intent_effects",
    )
    op.drop_table("conversation_intent_effects")
