"""Memory export, deletion, and preference update service."""

from app.db.config import database_settings
from app.db.engine import connect
from app.db.factory import repository_factory
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.db.session import initialize_database
from app.db.repositories import UserProfileRepository
from app.memory.settings_payload import load_user_memory_settings_payload
from app.memory.settings_store import UserMemorySettingsRepository
from app.models_memory import (
    MemoryPreferencesUpdateRequest,
    MemoryPreferencesUpdateResponse,
    PracticeSummaryConsentUpdateResponse,
    PracticePreferences,
    UserOnboardingProfile,
    UserOnboardingProfileResponse,
    UserConsentState,
    UserMemoryDeleteResponse,
    UserMemoryExportResponse,
    UserProfileResponse,
)


USER_MEMORY_TABLES = (
    "runs",
    "roleplay_sessions",
    "worksheets",
    "exposure_plans",
    "exposure_attempts",
    "protocols",
    "intervention_plans",
    "user_memory_settings",
    "session_reviews",
    "episodic_memories",
    "thread_checkpoints",
    "memory_events",
    "memory_proposals",
)
USER_MEMORY_DELETE_ORDER = (
    "memory_events",
    "memory_proposals",
    "thread_checkpoints",
    "episodic_memories",
    "runs",
    "conversation_events",
    "conversation_module_proposals",
    "conversation_context_summaries",
    "conversation_module_runs",
    "conversations",
    "conversation_deletion_receipts",
    "roleplay_sessions",
    "worksheets",
    "exposure_attempts",
    "exposure_plans",
    "protocols",
    "intervention_plans",
    "session_reviews",
    "user_memory_settings",
)


class MemoryPrivacyService:
    """Coordinate user-controlled memory export, deletion, and preferences."""

    def __init__(
        self,
        *,
        profile_repository: UserProfileRepository | None = None,
        settings_repository: UserMemorySettingsRepository | None = None,
        database_url: str | None = None,
    ) -> None:
        self.database_url = database_url or database_settings().database_url
        self.provider = resolve_database_provider(self.database_url)
        if self.provider == DatabaseProvider.SQLITE:
            initialize_database()
        factory = repository_factory(self.database_url)
        self.profile_repository = profile_repository or factory.user_profile_repository()
        self.settings_repository = settings_repository or factory.user_memory_settings_repository()

    def profile(self, user_id: str) -> UserProfileResponse:
        """Return a privacy-minimized profile with memory settings."""
        memory_settings = self.settings_repository.get(user_id)
        return UserProfileResponse(
            user_id=user_id,
            practice_summary=self.profile_repository.get_summary(user_id),
            consent_state=memory_settings.consent_state,
            practice_preferences=memory_settings.practice_preferences,
        )

    def export(self, user_id: str) -> UserMemoryExportResponse:
        """Export user-owned persisted records in JSON-compatible form."""
        if self.provider == DatabaseProvider.POSTGRES:
            records = self._export_postgres(user_id)
        else:
            records = self._export_sqlite(user_id)
        return UserMemoryExportResponse(
            user_id=user_id,
            profile=self.profile(user_id),
            records=records,
        )

    def delete(self, user_id: str) -> UserMemoryDeleteResponse:
        """Delete user-owned persisted records."""
        if self.provider == DatabaseProvider.POSTGRES:
            deleted_counts = self._delete_postgres(user_id)
        else:
            deleted_counts = self._delete_sqlite(user_id)
        return UserMemoryDeleteResponse(
            user_id=user_id,
            deleted_counts=deleted_counts,
            profile_after_delete=self.profile(user_id),
        )

    def update_preferences(
        self,
        *,
        user_id: str,
        request: MemoryPreferencesUpdateRequest,
    ) -> MemoryPreferencesUpdateResponse:
        """Save practice preferences only after explicit consent."""
        if not request.consent_to_save_preferences:
            raise PermissionError("Explicit consent_to_save_preferences=true is required.")
        current = self.settings_repository.get(user_id)
        consent_state = UserConsentState(
            consent_to_practice_summary=current.consent_state.consent_to_practice_summary,
            consent_to_save_preferences=True,
            do_not_store_raw_messages=True,
            allow_sensitive_memory=False,
        )
        settings = self.settings_repository.save(
            user_id=user_id,
            consent_state=consent_state,
            practice_preferences=request.practice_preferences,
        )
        return MemoryPreferencesUpdateResponse(
            user_id=user_id,
            consent_state=settings.consent_state,
            practice_preferences=settings.practice_preferences,
        )

    def disable_preferences(self, user_id: str) -> MemoryPreferencesUpdateResponse:
        """Turn off long-term practice preferences without deleting all memory."""
        current = self.settings_repository.get(user_id)
        consent_state = UserConsentState(
            consent_to_practice_summary=current.consent_state.consent_to_practice_summary,
            consent_to_save_preferences=False,
            do_not_store_raw_messages=True,
            allow_sensitive_memory=False,
        )
        settings = self.settings_repository.save(
            user_id=user_id,
            consent_state=consent_state,
            practice_preferences=PracticePreferences(),
        )
        return MemoryPreferencesUpdateResponse(
            user_id=user_id,
            consent_state=settings.consent_state,
            practice_preferences=settings.practice_preferences,
        )

    def update_practice_summary_consent(
        self,
        *,
        user_id: str,
        consent_to_practice_summary: bool,
    ) -> PracticeSummaryConsentUpdateResponse:
        """Enable or revoke future agent use of saved practice summaries."""
        current = self.settings_repository.get(user_id)
        consent_state = UserConsentState(
            consent_to_practice_summary=consent_to_practice_summary,
            consent_to_save_preferences=current.consent_state.consent_to_save_preferences,
            do_not_store_raw_messages=True,
            allow_sensitive_memory=False,
        )
        settings = self.settings_repository.save(
            user_id=user_id,
            consent_state=consent_state,
        )
        return PracticeSummaryConsentUpdateResponse(
            user_id=user_id,
            consent_state=settings.consent_state,
        )

    def get_onboarding_profile(self, user_id: str) -> UserOnboardingProfileResponse:
        """Return low-sensitivity onboarding choices."""
        settings = self.settings_repository.get(user_id)
        return UserOnboardingProfileResponse(
            user_id=user_id,
            onboarding_profile=settings.onboarding_profile,
        )

    def update_onboarding_profile(
        self,
        *,
        user_id: str,
        onboarding_profile: UserOnboardingProfile,
    ) -> UserOnboardingProfileResponse:
        """Save low-sensitivity onboarding profile fields."""
        settings = self.settings_repository.save(
            user_id=user_id,
            onboarding_profile=onboarding_profile,
        )
        return UserOnboardingProfileResponse(
            user_id=user_id,
            onboarding_profile=settings.onboarding_profile,
        )

    def reset_onboarding_profile(self, user_id: str) -> UserOnboardingProfileResponse:
        """Reset onboarding profile choices while preserving other memory settings."""
        settings = self.settings_repository.save(
            user_id=user_id,
            onboarding_profile=UserOnboardingProfile(),
        )
        return UserOnboardingProfileResponse(
            user_id=user_id,
            onboarding_profile=settings.onboarding_profile,
        )

    def _export_sqlite(self, user_id: str) -> dict[str, list[dict[str, object]]]:
        """Export user-owned SQLite rows."""
        records: dict[str, list[dict[str, object]]] = {}
        with connect() as connection:
            for table in USER_MEMORY_TABLES:
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
                records[table] = [
                    _sanitize_memory_settings_export_row(dict(row))
                    if table == "user_memory_settings"
                    else dict(row)
                    for row in rows
                ]
        return records

    def _delete_sqlite(self, user_id: str) -> dict[str, int]:
        """Delete user-owned SQLite rows in dependency-safe order."""
        deleted_counts: dict[str, int] = {}
        with connect() as connection:
            for table in USER_MEMORY_DELETE_ORDER:
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE user_id = ?",
                    (user_id,),
                )
                deleted_counts[table] = cursor.rowcount
        return deleted_counts

    def _export_postgres(self, user_id: str) -> dict[str, list[dict[str, object]]]:
        """Export user-owned PostgreSQL rows."""
        from sqlalchemy import create_engine, text

        engine = create_engine(self.database_url, pool_pre_ping=True)
        try:
            records: dict[str, list[dict[str, object]]] = {}
            with engine.connect() as connection:
                for table in USER_MEMORY_TABLES:
                    rows = connection.execute(
                        text(f"SELECT * FROM {table} WHERE user_id = :user_id"),
                        {"user_id": user_id},
                    ).mappings().all()
                    records[table] = [
                        _sanitize_memory_settings_export_row(_json_safe_row(dict(row)))
                        if table == "user_memory_settings"
                        else _json_safe_row(dict(row))
                        for row in rows
                    ]
            return records
        finally:
            engine.dispose()

    def _delete_postgres(self, user_id: str) -> dict[str, int]:
        """Delete user-owned PostgreSQL rows in dependency-safe order."""
        from sqlalchemy import create_engine, text

        engine = create_engine(self.database_url, pool_pre_ping=True)
        try:
            deleted_counts: dict[str, int] = {}
            with engine.begin() as connection:
                for table in USER_MEMORY_DELETE_ORDER:
                    result = connection.execute(
                        text(f"DELETE FROM {table} WHERE user_id = :user_id"),
                        {"user_id": user_id},
                    )
                    deleted_counts[table] = result.rowcount or 0
            return deleted_counts
        finally:
            engine.dispose()


def _json_safe_row(row: dict[str, object]) -> dict[str, object]:
    """Convert DB-driver row values into JSON-compatible export values."""
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }


def _sanitize_memory_settings_export_row(row: dict[str, object]) -> dict[str, object]:
    """Replace stored memory settings payload with sanitized export content."""
    payload = row.get("payload")
    if isinstance(payload, (str, dict)) or payload is None:
        settings = load_user_memory_settings_payload(payload)
    else:
        settings = load_user_memory_settings_payload(None)
    row["payload"] = settings.model_dump(mode="json")
    return row


memory_privacy_service = MemoryPrivacyService()
