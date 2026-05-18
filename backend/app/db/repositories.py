"""Repository interfaces plus SQLite and in-memory implementations."""

from typing import Protocol

from app.db.engine import connect
from app.db.session import initialize_database
from app.models import TraceRecord
from app.models_exposure import ExposureAttempt, ExposurePlan
from app.models_roleplay import RoleplaySession
from app.models_worksheet import WorksheetRecord
from app.models_memory import UserPracticeSummary


class TraceRepository(Protocol):
    """Persistence contract for workflow traces."""

    def save(self, record: TraceRecord) -> TraceRecord: ...
    def get(self, run_id: str) -> TraceRecord | None: ...


class RoleplaySessionRepository(Protocol):
    """Persistence contract for role-play sessions."""

    def save(self, session: RoleplaySession) -> RoleplaySession: ...
    def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None: ...


class WorksheetRepository(Protocol):
    """Persistence contract for worksheets."""

    def save(self, record: WorksheetRecord) -> WorksheetRecord: ...
    def get(self, worksheet_id: str) -> WorksheetRecord | None: ...


class ExposureRepository(Protocol):
    """Persistence contract for active exposure plans and attempts."""

    def save_plan(self, plan: ExposurePlan) -> ExposurePlan: ...
    def get_for_user(self, user_id: str) -> ExposurePlan | None: ...
    def save_attempt(self, user_id: str, plan: ExposurePlan, attempt: ExposureAttempt) -> ExposurePlan: ...


class UserProfileRepository(Protocol):
    """Persistence contract for privacy-minimized user practice summaries."""

    def get_summary(self, user_id: str) -> UserPracticeSummary: ...


class SQLiteTraceRepository:
    """SQLite-backed workflow trace repository."""

    def __init__(self) -> None:
        initialize_database()

    def save(self, record: TraceRecord) -> TraceRecord:
        with connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO runs (run_id, user_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (record.run_id, record.user_id, record.model_dump_json(), record.created_at.isoformat()),
            )
        return record

    def get(self, run_id: str) -> TraceRecord | None:
        with connect() as connection:
            row = connection.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return TraceRecord.model_validate_json(row["payload"]) if row else None


class SQLiteRoleplaySessionRepository:
    """SQLite-backed role-play session repository."""

    def __init__(self) -> None:
        initialize_database()

    def save(self, session: RoleplaySession) -> RoleplaySession:
        with connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO roleplay_sessions
                (session_id, user_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)""",
                (session.session_id, session.user_id, session.model_dump_json(), session.created_at.isoformat(), session.updated_at.isoformat()),
            )
        return session

    def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT payload FROM roleplay_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        return RoleplaySession.model_validate_json(row["payload"]) if row else None


class SQLiteWorksheetRepository:
    """SQLite-backed worksheet repository."""

    def __init__(self) -> None:
        initialize_database()

    def save(self, record: WorksheetRecord) -> WorksheetRecord:
        with connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO worksheets (worksheet_id, user_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (record.worksheet_id, record.user_id, record.model_dump_json(), record.created_at.isoformat()),
            )
        return record

    def get(self, worksheet_id: str) -> WorksheetRecord | None:
        with connect() as connection:
            row = connection.execute("SELECT payload FROM worksheets WHERE worksheet_id = ?", (worksheet_id,)).fetchone()
        return WorksheetRecord.model_validate_json(row["payload"]) if row else None


class SQLiteExposureRepository:
    """SQLite-backed active exposure plan repository."""

    def __init__(self) -> None:
        initialize_database()

    def save_plan(self, plan: ExposurePlan) -> ExposurePlan:
        with connect() as connection:
            existing = connection.execute(
                "SELECT plan_id FROM exposure_plans WHERE user_id = ?", (plan.user_id,)
            ).fetchone()
            if existing:
                connection.execute("DELETE FROM exposure_attempts WHERE plan_id = ?", (existing["plan_id"],))
            connection.execute(
                """INSERT OR REPLACE INTO exposure_plans
                (plan_id, user_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)""",
                (plan.plan_id, plan.user_id, plan.model_dump_json(), plan.created_at.isoformat(), plan.updated_at.isoformat()),
            )
        return plan

    def get_for_user(self, user_id: str) -> ExposurePlan | None:
        with connect() as connection:
            row = connection.execute("SELECT payload FROM exposure_plans WHERE user_id = ?", (user_id,)).fetchone()
        return ExposurePlan.model_validate_json(row["payload"]) if row else None

    def save_attempt(self, user_id: str, plan: ExposurePlan, attempt: ExposureAttempt) -> ExposurePlan:
        with connect() as connection:
            connection.execute(
                "INSERT INTO exposure_attempts (plan_id, user_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (plan.plan_id, user_id, attempt.model_dump_json(), attempt.created_at.isoformat()),
            )
            connection.execute(
                "UPDATE exposure_plans SET payload = ?, updated_at = ? WHERE plan_id = ? AND user_id = ?",
                (plan.model_dump_json(), plan.updated_at.isoformat(), plan.plan_id, user_id),
            )
        return plan


class SQLiteUserProfileRepository:
    """Build a lightweight summary from existing practice records."""

    def __init__(self) -> None:
        initialize_database()

    def get_summary(self, user_id: str) -> UserPracticeSummary:
        with connect() as connection:
            roleplay_rows = connection.execute(
                "SELECT payload FROM roleplay_sessions WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
            worksheet_count = connection.execute(
                "SELECT COUNT(*) AS count FROM worksheets WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]
            plan_row = connection.execute(
                "SELECT payload FROM exposure_plans WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        sessions = [RoleplaySession.model_validate_json(row["payload"]) for row in roleplay_rows]
        plan = ExposurePlan.model_validate_json(plan_row["payload"]) if plan_row else None
        recent_scenarios = list(dict.fromkeys(session.scenario.value for session in sessions))[:3]
        preferred_difficulty = sessions[0].difficulty if sessions else None
        latest_anxiety_level = None
        exposure_attempt_count = 0
        if plan is not None:
            exposure_attempt_count = len(plan.attempts)
            latest_anxiety_level = (
                plan.attempts[-1].anxiety_after
                if plan.attempts
                else plan.current_anxiety_level
            )
            if plan.target_scenario not in recent_scenarios:
                recent_scenarios = [plan.target_scenario, *recent_scenarios][:3]

        return UserPracticeSummary(
            recent_scenarios=recent_scenarios,
            roleplay_session_count=len(sessions),
            worksheet_count=worksheet_count,
            exposure_attempt_count=exposure_attempt_count,
            latest_anxiety_level=latest_anxiety_level,
            preferred_difficulty=preferred_difficulty,
        )

class InMemoryTraceRepository:
    """In-memory trace repository for tests."""
    def __init__(self) -> None: self.records: dict[str, TraceRecord] = {}
    def save(self, record: TraceRecord) -> TraceRecord: self.records[record.run_id] = record; return record
    def get(self, run_id: str) -> TraceRecord | None: return self.records.get(run_id)


class InMemoryRoleplaySessionRepository:
    """In-memory role-play repository for tests."""
    def __init__(self) -> None: self.sessions: dict[str, RoleplaySession] = {}
    def save(self, session: RoleplaySession) -> RoleplaySession: self.sessions[session.session_id] = session; return session
    def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        session = self.sessions.get(session_id)
        return session if session and session.user_id == user_id else None


class InMemoryWorksheetRepository:
    """In-memory worksheet repository for tests."""
    def __init__(self) -> None: self.worksheets: dict[str, WorksheetRecord] = {}
    def save(self, record: WorksheetRecord) -> WorksheetRecord: self.worksheets[record.worksheet_id] = record; return record
    def get(self, worksheet_id: str) -> WorksheetRecord | None: return self.worksheets.get(worksheet_id)


class InMemoryExposureRepository:
    """In-memory exposure repository for tests."""
    def __init__(self) -> None: self.plans_by_user: dict[str, ExposurePlan] = {}
    def save_plan(self, plan: ExposurePlan) -> ExposurePlan: self.plans_by_user[plan.user_id] = plan; return plan
    def get_for_user(self, user_id: str) -> ExposurePlan | None: return self.plans_by_user.get(user_id)
    def save_attempt(self, user_id: str, plan: ExposurePlan, attempt: ExposureAttempt) -> ExposurePlan:
        self.plans_by_user[user_id] = plan
        return plan
