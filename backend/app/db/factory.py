"""Repository factory for selecting persistence adapters by database provider."""

from typing import Protocol

from app.db.config import database_settings
from app.db.account_repositories import (
    AccountRepository,
    PostgresAccountRepository,
    SQLiteAccountRepository,
)
from app.db.postgres.exposure_repository import PostgresExposureRepository
from app.db.postgres.conversation_repository import PostgresConversationRepository
from app.db.postgres.intervention_plan_repository import PostgresInterventionPlanRepository
from app.db.postgres.metrics_repository import PostgresMetricsRepository
from app.db.postgres.memory_settings_repository import PostgresUserMemorySettingsRepository
from app.db.postgres.protocol_repository import PostgresProtocolRepository
from app.db.postgres.roleplay_repository import PostgresRoleplaySessionRepository
from app.db.postgres.session_review_repository import PostgresSessionReviewRepository
from app.db.postgres.trace_repository import PostgresTraceRepository
from app.db.postgres.user_profile_repository import PostgresUserProfileRepository
from app.db.postgres.worksheet_repository import PostgresWorksheetRepository
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.db.repositories import (
    ExposureRepository,
    RoleplaySessionRepository,
    SessionReviewRepository,
    SQLiteExposureRepository,
    SQLiteRoleplaySessionRepository,
    SQLiteSessionReviewRepository,
    SQLiteTraceRepository,
    SQLiteUserProfileRepository,
    SQLiteWorksheetRepository,
    TraceRepository,
    UserProfileRepository,
    WorksheetRepository,
)
from app.models_protocols import ProtocolRecord, ProtocolStatus, ProtocolType
from app.memory.intervention_plan_store import InterventionPlanStore
from app.memory.long_term_repository import (
    LongTermMemoryRepository,
    SQLiteLongTermMemoryRepository,
)
from app.memory.proposal_repository import (
    MemoryProposalRepository,
    SQLiteMemoryProposalRepository,
)
from app.memory.settings_store import (
    SQLiteUserMemorySettingsRepository,
    UserMemorySettingsRepository,
)
from app.observability.metrics import MetricsRepository, SQLiteMetricsRepository
from app.protocols.store import ProtocolStore
from app.conversation.repository import (
    ConversationRepository,
    SQLiteConversationRepository,
)


class ProtocolRepository(Protocol):
    """Persistence contract for consent protocol records."""

    async def create(
        self,
        *,
        user_id: str,
        protocol_type: ProtocolType,
        session_id: str | None,
        harness_action: str,
        request_hash: str,
        expires_at,
        payload: dict[str, object],
    ) -> ProtocolRecord: ...

    async def save(self, record: ProtocolRecord) -> ProtocolRecord: ...
    async def get_for_user(
        self,
        protocol_id: str,
        user_id: str,
    ) -> ProtocolRecord | None: ...
    async def set_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        status: ProtocolStatus,
    ) -> ProtocolRecord | None: ...
    async def transition_status(
        self,
        *,
        protocol_id: str,
        user_id: str,
        expected_status: ProtocolStatus,
        next_status: ProtocolStatus,
    ) -> ProtocolRecord | None: ...
    async def expire_pending_before(self, cutoff) -> int: ...


class RepositoryNotImplementedError(NotImplementedError):
    """Raised when a provider is valid but a repository adapter is not available."""


class RepositoryFactory:
    """Create repositories for the configured database provider."""

    def __init__(self, database_url: str | None = None) -> None:
        settings = database_settings()
        self.database_url = database_url or settings.database_url
        self.provider = resolve_database_provider(self.database_url)

    def trace_repository(self) -> TraceRepository:
        """Return the trace repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteTraceRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresTraceRepository(database_url=self.database_url)
        raise self._not_implemented("trace")

    def conversation_repository(self) -> ConversationRepository:
        """Return the unified conversation repository."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteConversationRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresConversationRepository(database_url=self.database_url)
        raise self._not_implemented("conversation")

    def roleplay_repository(self) -> RoleplaySessionRepository:
        """Return the role-play session repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteRoleplaySessionRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresRoleplaySessionRepository(database_url=self.database_url)
        raise self._not_implemented("roleplay session")

    def worksheet_repository(self) -> WorksheetRepository:
        """Return the worksheet repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteWorksheetRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresWorksheetRepository(database_url=self.database_url)
        raise self._not_implemented("worksheet")

    def exposure_repository(self) -> ExposureRepository:
        """Return the exposure plan repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteExposureRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresExposureRepository(database_url=self.database_url)
        raise self._not_implemented("exposure")

    def metrics_repository(self) -> MetricsRepository:
        """Return the aggregate metrics repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteMetricsRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresMetricsRepository(database_url=self.database_url)
        raise self._not_implemented("metrics")

    def account_repository(self) -> AccountRepository:
        """Return the account repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteAccountRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresAccountRepository(database_url=self.database_url)
        raise self._not_implemented("account")

    def user_profile_repository(self) -> UserProfileRepository:
        """Return the user profile repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteUserProfileRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresUserProfileRepository(database_url=self.database_url)
        raise self._not_implemented("user profile")

    def user_memory_settings_repository(self) -> UserMemorySettingsRepository:
        """Return the memory settings repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteUserMemorySettingsRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresUserMemorySettingsRepository(database_url=self.database_url)
        raise self._not_implemented("user memory settings")

    def session_review_repository(self) -> SessionReviewRepository:
        """Return the session review repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteSessionReviewRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresSessionReviewRepository(database_url=self.database_url)
        raise self._not_implemented("session review")

    def long_term_memory_repository(self) -> LongTermMemoryRepository:
        """Return the durable episodic-memory and checkpoint repository."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteLongTermMemoryRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            from app.db.postgres.long_term_memory_repository import (
                PostgresLongTermMemoryRepository,
            )

            return PostgresLongTermMemoryRepository(database_url=self.database_url)
        raise self._not_implemented("long-term memory")

    def memory_proposal_repository(self) -> MemoryProposalRepository:
        """Return the confirmation-gated memory proposal repository."""
        if self.provider == DatabaseProvider.SQLITE:
            return SQLiteMemoryProposalRepository()
        if self.provider == DatabaseProvider.POSTGRES:
            from app.db.postgres.memory_proposal_repository import (
                PostgresMemoryProposalRepository,
            )

            return PostgresMemoryProposalRepository(database_url=self.database_url)
        raise self._not_implemented("memory proposal")

    def protocol_repository(self) -> ProtocolRepository:
        """Return the protocol repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return ProtocolStore()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresProtocolRepository(database_url=self.database_url)
        raise self._not_implemented("protocol")

    def intervention_plan_repository(self) -> InterventionPlanStore | PostgresInterventionPlanRepository:
        """Return the intervention plan repository for the configured provider."""
        if self.provider == DatabaseProvider.SQLITE:
            return InterventionPlanStore()
        if self.provider == DatabaseProvider.POSTGRES:
            return PostgresInterventionPlanRepository(database_url=self.database_url)
        raise self._not_implemented("intervention plan")

    def _not_implemented(self, repository_name: str) -> RepositoryNotImplementedError:
        return RepositoryNotImplementedError(
            f"{repository_name} repository is not implemented for provider "
            f"{self.provider.value!r}."
        )


def repository_factory(database_url: str | None = None) -> RepositoryFactory:
    """Return a repository factory for the current process settings."""
    return RepositoryFactory(database_url=database_url)
