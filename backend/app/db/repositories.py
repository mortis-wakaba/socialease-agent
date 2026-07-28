"""Repository interfaces plus SQLite and in-memory implementations."""

from typing import Protocol

from app.db.config import database_settings
from app.db.engine import connect
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.db.session import initialize_database
from app.models import TraceRecord
from app.models_exposure import ExposureAttempt, ExposurePlan
from app.models_roleplay import RoleplaySession
from app.models_session_review import SessionReviewRecord
from app.models_worksheet import WorksheetRecord
from app.models_memory import UserPracticeSummary
from app.memory.profile_projection import build_user_practice_summary


def _initialize_sqlite_if_configured() -> None:
    """Initialize local SQLite tables only when SQLite is the active provider."""
    if resolve_database_provider(database_settings().database_url) == DatabaseProvider.SQLITE:
        initialize_database()


class TraceRepository(Protocol):
    """Persistence contract for workflow traces."""

    async def save(self, record: TraceRecord) -> TraceRecord: ...
    async def get(self, run_id: str) -> TraceRecord | None: ...
    async def list_recent(self, limit: int = 100) -> list[TraceRecord]: ...


class RoleplaySessionRepository(Protocol):
    """Persistence contract for role-play sessions."""

    async def save(self, session: RoleplaySession) -> RoleplaySession: ...
    async def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None: ...
    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RoleplaySession]: ...


class WorksheetRepository(Protocol):
    """Persistence contract for worksheets."""

    async def save(self, record: WorksheetRecord) -> WorksheetRecord: ...
    async def get(self, worksheet_id: str) -> WorksheetRecord | None: ...


class ExposureRepository(Protocol):
    """Persistence contract for active exposure plans and attempts."""

    async def save_plan(self, plan: ExposurePlan) -> ExposurePlan: ...
    async def get_for_user(self, user_id: str) -> ExposurePlan | None: ...
    async def get_by_id_for_user(self, plan_id: str, user_id: str) -> ExposurePlan | None: ...
    async def save_attempt(self, user_id: str, plan: ExposurePlan, attempt: ExposureAttempt) -> ExposurePlan: ...


class UserProfileRepository(Protocol):
    """Persistence contract for privacy-minimized user practice summaries."""

    async def get_summary(self, user_id: str) -> UserPracticeSummary: ...


class SessionReviewRepository(Protocol):
    """Persistence contract for privacy-safe session reviews."""

    async def save(self, record: SessionReviewRecord) -> SessionReviewRecord: ...
    async def list_for_user(self, user_id: str, limit: int = 20) -> list[SessionReviewRecord]: ...


class SQLiteTraceRepository:
    """SQLite-backed workflow trace repository."""

    def __init__(self) -> None:
        _initialize_sqlite_if_configured()

    async def save(self, record: TraceRecord) -> TraceRecord:
        with connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO runs (run_id, user_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (record.run_id, record.user_id, record.model_dump_json(), record.created_at.isoformat()),
            )
        return record

    async def get(self, run_id: str) -> TraceRecord | None:
        with connect() as connection:
            row = connection.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return TraceRecord.model_validate_json(row["payload"]) if row else None

    async def list_recent(self, limit: int = 100) -> list[TraceRecord]:
        """Return recent trace records ordered from newest to oldest."""
        with connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [TraceRecord.model_validate_json(row["payload"]) for row in rows]


class SQLiteRoleplaySessionRepository:
    """SQLite-backed role-play session repository."""

    def __init__(self) -> None:
        _initialize_sqlite_if_configured()

    async def save(self, session: RoleplaySession) -> RoleplaySession:
        with connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO roleplay_sessions
                (session_id, user_id, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)""",
                (session.session_id, session.user_id, session.model_dump_json(), session.created_at.isoformat(), session.updated_at.isoformat()),
            )
        return session

    async def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT payload FROM roleplay_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        return RoleplaySession.model_validate_json(row["payload"]) if row else None

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RoleplaySession]:
        """Return recent role-play sessions for one user."""
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM roleplay_sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?""",
                (user_id, limit, offset),
            ).fetchall()
        return [RoleplaySession.model_validate_json(row["payload"]) for row in rows]


class SQLiteWorksheetRepository:
    """SQLite-backed worksheet repository."""

    def __init__(self) -> None:
        _initialize_sqlite_if_configured()

    async def save(self, record: WorksheetRecord) -> WorksheetRecord:
        with connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO worksheets (worksheet_id, user_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (record.worksheet_id, record.user_id, record.model_dump_json(), record.created_at.isoformat()),
            )
        return record

    async def get(self, worksheet_id: str) -> WorksheetRecord | None:
        with connect() as connection:
            row = connection.execute("SELECT payload FROM worksheets WHERE worksheet_id = ?", (worksheet_id,)).fetchone()
        return WorksheetRecord.model_validate_json(row["payload"]) if row else None


class SQLiteExposureRepository:
    """SQLite-backed active exposure plan repository."""

    def __init__(self) -> None:
        _initialize_sqlite_if_configured()

    async def save_plan(self, plan: ExposurePlan) -> ExposurePlan:
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

    async def get_for_user(self, user_id: str) -> ExposurePlan | None:
        with connect() as connection:
            row = connection.execute("SELECT payload FROM exposure_plans WHERE user_id = ?", (user_id,)).fetchone()
        return ExposurePlan.model_validate_json(row["payload"]) if row else None

    async def get_by_id_for_user(self, plan_id: str, user_id: str) -> ExposurePlan | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT payload FROM exposure_plans WHERE plan_id = ? AND user_id = ?",
                (plan_id, user_id),
            ).fetchone()
        return ExposurePlan.model_validate_json(row["payload"]) if row else None

    async def save_attempt(self, user_id: str, plan: ExposurePlan, attempt: ExposureAttempt) -> ExposurePlan:
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
        _initialize_sqlite_if_configured()

    async def get_summary(self, user_id: str) -> UserPracticeSummary:
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
        return build_user_practice_summary(
            sessions=sessions,
            worksheet_count=worksheet_count,
            exposure_plan=plan,
        )


class SQLiteSessionReviewRepository:
    """SQLite-backed structured session review repository."""

    def __init__(self) -> None:
        _initialize_sqlite_if_configured()

    async def save(self, record: SessionReviewRecord) -> SessionReviewRecord:
        with connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO session_reviews
                (review_id, user_id, source, source_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.review_id,
                    record.user_id,
                    record.source,
                    record.source_id,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                ),
            )
        return record

    async def list_for_user(self, user_id: str, limit: int = 20) -> list[SessionReviewRecord]:
        with connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM session_reviews
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [SessionReviewRecord.model_validate_json(row["payload"]) for row in rows]

class InMemoryTraceRepository:
    """In-memory trace repository for tests."""
    def __init__(self) -> None: self.records: dict[str, TraceRecord] = {}
    async def save(self, record: TraceRecord) -> TraceRecord: self.records[record.run_id] = record; return record
    async def get(self, run_id: str) -> TraceRecord | None: return self.records.get(run_id)
    async def list_recent(self, limit: int = 100) -> list[TraceRecord]:
        return sorted(
            self.records.values(),
            key=lambda record: record.created_at,
            reverse=True,
        )[:limit]


class InMemoryRoleplaySessionRepository:
    """In-memory role-play repository for tests."""
    def __init__(self) -> None: self.sessions: dict[str, RoleplaySession] = {}
    async def save(self, session: RoleplaySession) -> RoleplaySession: self.sessions[session.session_id] = session; return session
    async def get_for_user(self, session_id: str, user_id: str) -> RoleplaySession | None:
        session = self.sessions.get(session_id)
        return session if session and session.user_id == user_id else None
    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RoleplaySession]:
        sessions = [session for session in self.sessions.values() if session.user_id == user_id]
        ordered = sorted(sessions, key=lambda session: session.updated_at, reverse=True)
        return ordered[offset : offset + limit]


class InMemoryWorksheetRepository:
    """In-memory worksheet repository for tests."""
    def __init__(self) -> None: self.worksheets: dict[str, WorksheetRecord] = {}
    async def save(self, record: WorksheetRecord) -> WorksheetRecord: self.worksheets[record.worksheet_id] = record; return record
    async def get(self, worksheet_id: str) -> WorksheetRecord | None: return self.worksheets.get(worksheet_id)


class InMemoryExposureRepository:
    """In-memory exposure repository for tests."""
    def __init__(self) -> None: self.plans_by_user: dict[str, ExposurePlan] = {}
    async def save_plan(self, plan: ExposurePlan) -> ExposurePlan: self.plans_by_user[plan.user_id] = plan; return plan
    async def get_for_user(self, user_id: str) -> ExposurePlan | None: return self.plans_by_user.get(user_id)
    async def get_by_id_for_user(self, plan_id: str, user_id: str) -> ExposurePlan | None:
        plan = self.plans_by_user.get(user_id)
        return plan if plan and plan.plan_id == plan_id else None
    async def save_attempt(self, user_id: str, plan: ExposurePlan, attempt: ExposureAttempt) -> ExposurePlan:
        self.plans_by_user[user_id] = plan
        return plan


class InMemorySessionReviewRepository:
    """In-memory session review repository for tests."""

    def __init__(self) -> None:
        self.reviews: dict[str, SessionReviewRecord] = {}

    async def save(self, record: SessionReviewRecord) -> SessionReviewRecord:
        self.reviews[record.review_id] = record
        return record

    async def list_for_user(self, user_id: str, limit: int = 20) -> list[SessionReviewRecord]:
        records = [
            record for record in self.reviews.values() if record.user_id == user_id
        ]
        return sorted(records, key=lambda record: record.created_at, reverse=True)[:limit]
