"""Privacy-aware memory settings store."""

from datetime import datetime, timezone
from typing import Protocol

from app.db.config import database_settings
from app.db.engine import connect
from app.db.providers import DatabaseProvider, resolve_database_provider
from app.db.session import initialize_database
from app.models_memory import (
    AgentMemoryType,
    PracticePreferences,
    UserOnboardingProfile,
    UserConsentState,
    UserMemorySettings,
)
from app.memory.settings_payload import load_user_memory_settings_payload


class UserMemorySettingsRepository(Protocol):
    """Persistence contract for privacy-aware memory settings."""

    def get(self, user_id: str) -> UserMemorySettings: ...
    def save(
        self,
        *,
        user_id: str,
        consent_state: UserConsentState | None = None,
        practice_preferences: PracticePreferences | None = None,
        onboarding_profile: UserOnboardingProfile | None = None,
        disabled_memory_types: list[AgentMemoryType] | None = None,
    ) -> UserMemorySettings: ...


class SQLiteUserMemorySettingsRepository:
    """SQLite-backed low-sensitivity memory settings repository."""

    def __init__(self) -> None:
        if resolve_database_provider(database_settings().database_url) == DatabaseProvider.SQLITE:
            initialize_database()

    def get(self, user_id: str) -> UserMemorySettings:
        """Return memory settings or privacy-preserving defaults."""
        with connect() as connection:
            row = connection.execute(
                "SELECT payload FROM user_memory_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return load_user_memory_settings_payload(row["payload"] if row else None)

    def save(
        self,
        *,
        user_id: str,
        consent_state: UserConsentState | None = None,
        practice_preferences: PracticePreferences | None = None,
        onboarding_profile: UserOnboardingProfile | None = None,
        disabled_memory_types: list[AgentMemoryType] | None = None,
    ) -> UserMemorySettings:
        """Persist explicit user memory settings."""
        current = self.get(user_id)
        settings = UserMemorySettings(
            consent_state=consent_state or current.consent_state,
            practice_preferences=practice_preferences or current.practice_preferences,
            onboarding_profile=onboarding_profile or current.onboarding_profile,
            disabled_memory_types=(
                disabled_memory_types
                if disabled_memory_types is not None
                else current.disabled_memory_types
            ),
        )
        with connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO user_memory_settings
                (user_id, payload, updated_at) VALUES (?, ?, ?)""",
                (
                    user_id,
                    settings.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return settings
