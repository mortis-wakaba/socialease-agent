"""Repository contract for durable user memory settings."""

from typing import Protocol

from app.models_memory import (
    AgentMemoryType,
    PracticePreferences,
    UserConsentState,
    UserMemorySettings,
    UserOnboardingProfile,
)


class UserMemorySettingsRepository(Protocol):
    """Persistence contract for user-controlled memory settings."""

    async def get(self, user_id: str) -> UserMemorySettings: ...

    async def save(
        self,
        *,
        user_id: str,
        consent_state: UserConsentState | None = None,
        practice_preferences: PracticePreferences | None = None,
        onboarding_profile: UserOnboardingProfile | None = None,
        disabled_memory_types: list[AgentMemoryType] | None = None,
    ) -> UserMemorySettings: ...


class InMemoryUserMemorySettingsRepository:
    """Non-persistent memory-settings fake for unit tests and evals."""

    def __init__(self) -> None:
        self._settings: dict[str, UserMemorySettings] = {}

    async def get(self, user_id: str) -> UserMemorySettings:
        """Return saved settings or the domain default."""
        return self._settings.get(user_id, UserMemorySettings())

    async def save(
        self,
        *,
        user_id: str,
        consent_state: UserConsentState | None = None,
        practice_preferences: PracticePreferences | None = None,
        onboarding_profile: UserOnboardingProfile | None = None,
        disabled_memory_types: list[AgentMemoryType] | None = None,
    ) -> UserMemorySettings:
        """Merge and save one user's settings."""
        current = await self.get(user_id)
        settings = current.model_copy(
            update={
                "consent_state": consent_state or current.consent_state,
                "practice_preferences": (
                    practice_preferences or current.practice_preferences
                ),
                "onboarding_profile": (
                    onboarding_profile or current.onboarding_profile
                ),
                "disabled_memory_types": (
                    disabled_memory_types
                    if disabled_memory_types is not None
                    else current.disabled_memory_types
                ),
            }
        )
        self._settings[user_id] = settings
        return settings
