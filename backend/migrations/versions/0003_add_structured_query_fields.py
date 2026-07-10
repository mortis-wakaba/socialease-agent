"""Add structured query fields for production-shaped PostgreSQL records.

Revision ID: 0003_add_structured_query_fields
Revises: 0002_add_harness_metric_events
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_add_structured_query_fields"
down_revision = "0002_add_harness_metric_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add query-friendly columns while preserving JSON payloads."""
    op.add_column("runs", sa.Column("risk_level", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("intent", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("selected_agent", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("permission_action", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("session_id", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("intervention_plan_id", sa.String(), nullable=True))
    op.create_index("idx_runs_risk_created", "runs", ["risk_level", "created_at"])
    op.create_index("idx_runs_intent_created", "runs", ["intent", "created_at"])
    op.create_index("idx_runs_user_created", "runs", ["user_id", "created_at"])

    op.add_column("roleplay_sessions", sa.Column("scenario", sa.String(), nullable=True))
    op.add_column("roleplay_sessions", sa.Column("difficulty", sa.Integer(), nullable=True))
    op.create_index(
        "idx_roleplay_sessions_user_updated",
        "roleplay_sessions",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "idx_roleplay_sessions_scenario",
        "roleplay_sessions",
        ["scenario"],
    )

    op.add_column("exposure_plans", sa.Column("current_anxiety_level", sa.Integer(), nullable=True))
    op.add_column("exposure_plans", sa.Column("recommended_next_task_id", sa.String(), nullable=True))
    op.add_column("exposure_plans", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "idx_exposure_plans_user_updated",
        "exposure_plans",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "idx_exposure_plans_deleted",
        "exposure_plans",
        ["deleted_at"],
    )

    op.add_column("exposure_attempts", sa.Column("task_id", sa.String(), nullable=True))
    op.add_column("exposure_attempts", sa.Column("status", sa.String(), nullable=True))
    op.add_column("exposure_attempts", sa.Column("anxiety_before", sa.Integer(), nullable=True))
    op.add_column("exposure_attempts", sa.Column("anxiety_after", sa.Integer(), nullable=True))
    op.create_index(
        "idx_exposure_attempts_status",
        "exposure_attempts",
        ["status"],
    )


def downgrade() -> None:
    """Drop structured query fields."""
    op.drop_index("idx_exposure_attempts_status", table_name="exposure_attempts")
    op.drop_column("exposure_attempts", "anxiety_after")
    op.drop_column("exposure_attempts", "anxiety_before")
    op.drop_column("exposure_attempts", "status")
    op.drop_column("exposure_attempts", "task_id")

    op.drop_index("idx_exposure_plans_deleted", table_name="exposure_plans")
    op.drop_index("idx_exposure_plans_user_updated", table_name="exposure_plans")
    op.drop_column("exposure_plans", "deleted_at")
    op.drop_column("exposure_plans", "recommended_next_task_id")
    op.drop_column("exposure_plans", "current_anxiety_level")

    op.drop_index("idx_roleplay_sessions_scenario", table_name="roleplay_sessions")
    op.drop_index("idx_roleplay_sessions_user_updated", table_name="roleplay_sessions")
    op.drop_column("roleplay_sessions", "difficulty")
    op.drop_column("roleplay_sessions", "scenario")

    op.drop_index("idx_runs_user_created", table_name="runs")
    op.drop_index("idx_runs_intent_created", table_name="runs")
    op.drop_index("idx_runs_risk_created", table_name="runs")
    op.drop_column("runs", "intervention_plan_id")
    op.drop_column("runs", "session_id")
    op.drop_column("runs", "permission_action")
    op.drop_column("runs", "selected_agent")
    op.drop_column("runs", "intent")
    op.drop_column("runs", "risk_level")
