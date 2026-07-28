"""PostgreSQL memory settings repository implementation."""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.config import database_settings
from app.db.postgres.engine import shared_postgres_async_engine
from app.models_memory import (
    AgentMemoryType,
    PracticePreferences,
    UserOnboardingProfile,
    UserConsentState,
    UserMemorySettings,
)
from app.memory.settings_payload import load_user_memory_settings_payload


class PostgresUserMemorySettingsRepository:
    """PostgreSQL-backed privacy-aware memory settings repository."""

    def __init__(
        self,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.engine = engine or shared_postgres_async_engine(
            database_url or database_settings().database_url
        )

    async def get(self, user_id: str) -> UserMemorySettings:
        """Return memory settings or privacy-preserving defaults."""
        async with self.engine.connect() as connection:
            row = (await connection.execute(
                text("SELECT payload FROM user_memory_settings WHERE user_id = :user_id"),
                {"user_id": user_id},
            )).mappings().first()
        return load_user_memory_settings_payload(row["payload"] if row else None)

    async def save(
        self,
        *,
        user_id: str,
        consent_state: UserConsentState | None = None,
        practice_preferences: PracticePreferences | None = None,
        onboarding_profile: UserOnboardingProfile | None = None,
        disabled_memory_types: list[AgentMemoryType] | None = None,
    ) -> UserMemorySettings:
        """Persist explicit user memory settings."""
        current = await self.get(user_id)
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
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """INSERT INTO user_memory_settings
                    (user_id, payload, updated_at)
                    VALUES (:user_id, CAST(:payload AS jsonb), :updated_at)
                    ON CONFLICT (user_id) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at"""
                ),
                {
                    "user_id": user_id,
                    "payload": settings.model_dump_json(),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
        return settings
