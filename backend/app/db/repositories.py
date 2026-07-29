"""Database-independent repository contracts and lightweight test fakes."""

from typing import Protocol

from app.models import TraceRecord
from app.models_exposure import ExposureAttempt, ExposurePlan
from app.models_memory import UserPracticeSummary
from app.models_roleplay import RoleplaySession
from app.models_session_review import SessionReviewRecord
from app.models_worksheet import WorksheetRecord


class TraceRepository(Protocol):
    """Persistence contract for workflow traces."""

    async def save(self, record: TraceRecord) -> TraceRecord: ...
    async def get(self, run_id: str) -> TraceRecord | None: ...
    async def list_recent(self, limit: int = 100) -> list[TraceRecord]: ...


class RoleplaySessionRepository(Protocol):
    """Persistence contract for role-play sessions."""

    async def save(self, session: RoleplaySession) -> RoleplaySession: ...
    async def get_for_user(
        self,
        session_id: str,
        user_id: str,
    ) -> RoleplaySession | None: ...
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
    async def get_by_id_for_user(
        self,
        plan_id: str,
        user_id: str,
    ) -> ExposurePlan | None: ...
    async def save_attempt(
        self,
        user_id: str,
        plan: ExposurePlan,
        attempt: ExposureAttempt,
    ) -> ExposurePlan: ...


class UserProfileRepository(Protocol):
    """Persistence contract for privacy-minimized practice summaries."""

    async def get_summary(self, user_id: str) -> UserPracticeSummary: ...


class SessionReviewRepository(Protocol):
    """Persistence contract for privacy-safe session reviews."""

    async def save(self, record: SessionReviewRecord) -> SessionReviewRecord: ...
    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[SessionReviewRecord]: ...


class InMemoryTraceRepository:
    """Non-persistent trace fake for unit tests."""

    def __init__(self) -> None:
        self.records: dict[str, TraceRecord] = {}

    async def save(self, record: TraceRecord) -> TraceRecord:
        self.records[record.run_id] = record
        return record

    async def get(self, run_id: str) -> TraceRecord | None:
        return self.records.get(run_id)

    async def list_recent(self, limit: int = 100) -> list[TraceRecord]:
        return sorted(
            self.records.values(),
            key=lambda record: record.created_at,
            reverse=True,
        )[:limit]


class InMemoryRoleplaySessionRepository:
    """Non-persistent role-play fake for unit tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, RoleplaySession] = {}

    async def save(self, session: RoleplaySession) -> RoleplaySession:
        self.sessions[session.session_id] = session
        return session

    async def get_for_user(
        self,
        session_id: str,
        user_id: str,
    ) -> RoleplaySession | None:
        session = self.sessions.get(session_id)
        return session if session and session.user_id == user_id else None

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RoleplaySession]:
        sessions = [
            session
            for session in self.sessions.values()
            if session.user_id == user_id
        ]
        ordered = sorted(
            sessions,
            key=lambda session: session.updated_at,
            reverse=True,
        )
        return ordered[offset : offset + limit]


class InMemoryWorksheetRepository:
    """Non-persistent worksheet fake for unit tests."""

    def __init__(self) -> None:
        self.worksheets: dict[str, WorksheetRecord] = {}

    async def save(self, record: WorksheetRecord) -> WorksheetRecord:
        self.worksheets[record.worksheet_id] = record
        return record

    async def get(self, worksheet_id: str) -> WorksheetRecord | None:
        return self.worksheets.get(worksheet_id)


class InMemoryExposureRepository:
    """Non-persistent exposure fake for unit tests."""

    def __init__(self) -> None:
        self.plans_by_user: dict[str, ExposurePlan] = {}

    async def save_plan(self, plan: ExposurePlan) -> ExposurePlan:
        self.plans_by_user[plan.user_id] = plan
        return plan

    async def get_for_user(self, user_id: str) -> ExposurePlan | None:
        return self.plans_by_user.get(user_id)

    async def get_by_id_for_user(
        self,
        plan_id: str,
        user_id: str,
    ) -> ExposurePlan | None:
        plan = self.plans_by_user.get(user_id)
        return plan if plan and plan.plan_id == plan_id else None

    async def save_attempt(
        self,
        user_id: str,
        plan: ExposurePlan,
        attempt: ExposureAttempt,
    ) -> ExposurePlan:
        del attempt
        self.plans_by_user[user_id] = plan
        return plan


class InMemorySessionReviewRepository:
    """Non-persistent session-review fake for unit tests."""

    def __init__(self) -> None:
        self.reviews: dict[str, SessionReviewRecord] = {}

    async def save(self, record: SessionReviewRecord) -> SessionReviewRecord:
        self.reviews[record.review_id] = record
        return record

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[SessionReviewRecord]:
        records = [
            record
            for record in self.reviews.values()
            if record.user_id == user_id
        ]
        return sorted(
            records,
            key=lambda record: record.created_at,
            reverse=True,
        )[:limit]
