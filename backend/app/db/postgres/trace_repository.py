"""PostgreSQL trace repository implementation."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.config import database_settings
from app.models import TraceRecord


class PostgresTraceRepository:
    """PostgreSQL-backed workflow trace repository."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            database_url or database_settings().database_url,
            pool_pre_ping=True,
        )

    def save(self, record: TraceRecord) -> TraceRecord:
        """Persist one product-safe workflow trace."""
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO runs
                    (run_id, user_id, product_safe, risk_level, intent, selected_agent,
                    permission_action, session_id, intervention_plan_id, payload, created_at)
                    VALUES
                    (:run_id, :user_id, :product_safe, :risk_level, :intent, :selected_agent,
                    :permission_action, :session_id, :intervention_plan_id,
                    CAST(:payload AS jsonb), :created_at)
                    ON CONFLICT (run_id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        product_safe = EXCLUDED.product_safe,
                        risk_level = EXCLUDED.risk_level,
                        intent = EXCLUDED.intent,
                        selected_agent = EXCLUDED.selected_agent,
                        permission_action = EXCLUDED.permission_action,
                        session_id = EXCLUDED.session_id,
                        intervention_plan_id = EXCLUDED.intervention_plan_id,
                        payload = EXCLUDED.payload,
                        created_at = EXCLUDED.created_at"""
                ),
                _trace_params(record),
            )
        return record

    def get(self, run_id: str) -> TraceRecord | None:
        """Return one workflow trace by run id."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT payload FROM runs WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).mappings().first()
        return TraceRecord.model_validate(row["payload"]) if row else None

    def list_recent(self, limit: int = 100) -> list[TraceRecord]:
        """Return recent workflow traces ordered from newest to oldest."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """SELECT payload FROM runs
                    ORDER BY created_at DESC
                    LIMIT :limit"""
                ),
                {"limit": limit},
            ).mappings().all()
        return [TraceRecord.model_validate(row["payload"]) for row in rows]


def _trace_params(record: TraceRecord) -> dict[str, object]:
    """Return SQL parameters for a workflow trace."""
    return {
        "run_id": record.run_id,
        "user_id": record.user_id,
        "product_safe": record.product_safe,
        "risk_level": record.safety_result.risk_level.value,
        "intent": record.intent_result.intent.value,
        "selected_agent": record.selected_agent,
        "permission_action": record.permission_action,
        "session_id": record.session_id,
        "intervention_plan_id": record.intervention_plan_id,
        "payload": record.model_dump_json(),
        "created_at": record.created_at,
    }
