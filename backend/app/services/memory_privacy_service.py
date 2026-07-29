"""Memory export, deletion, and preference update service."""

from app.db.factory import repository_factory
from app.db.repositories import UserProfileRepository
from app.memory.privacy_repository import (
    MemoryPrivacyRepository,
    UserDataDeleteScope,
)
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


class MemoryPrivacyService:
    """Coordinate user-controlled memory export, deletion, and preferences."""

    def __init__(
        self,
        *,
        profile_repository: UserProfileRepository | None = None,
        settings_repository: UserMemorySettingsRepository | None = None,
        privacy_repository: MemoryPrivacyRepository | None = None,
        database_url: str | None = None,
    ) -> None:
        factory = repository_factory(database_url)
        self.profile_repository = profile_repository or factory.user_profile_repository()
        self.settings_repository = settings_repository or factory.user_memory_settings_repository()
        self.privacy_repository = (
            privacy_repository or factory.memory_privacy_repository()
        )

    async def profile(self, user_id: str) -> UserProfileResponse:
        """Return a privacy-minimized profile with memory settings."""
        memory_settings = await self.settings_repository.get(user_id)
        return UserProfileResponse(
            user_id=user_id,
            practice_summary=await self.profile_repository.get_summary(user_id),
            consent_state=memory_settings.consent_state,
            practice_preferences=memory_settings.practice_preferences,
        )

    async def export(self, user_id: str) -> UserMemoryExportResponse:
        """Export user-owned persisted records in JSON-compatible form."""
        records = await self.privacy_repository.export_agent_memory(
            user_id=user_id
        )
        return UserMemoryExportResponse(
            user_id=user_id,
            profile=await self.profile(user_id),
            records=records,
        )

    async def delete(self, user_id: str) -> UserMemoryDeleteResponse:
        """Delete only cross-conversation agent memory and personalization."""
        deleted_counts = await self.privacy_repository.delete_user_data(
            user_id=user_id,
            scope=UserDataDeleteScope.AGENT_MEMORY,
        )
        return UserMemoryDeleteResponse(
            user_id=user_id,
            deleted_counts=deleted_counts,
            profile_after_delete=await self.profile(user_id),
        )

    async def delete_all_user_data(
        self,
        user_id: str,
    ) -> UserMemoryDeleteResponse:
        """Delete every durable user-owned product record for account erasure."""
        deleted_counts = await self.privacy_repository.delete_user_data(
            user_id=user_id,
            scope=UserDataDeleteScope.ACCOUNT,
        )
        return UserMemoryDeleteResponse(
            user_id=user_id,
            deleted_counts=deleted_counts,
            profile_after_delete=await self.profile(user_id),
        )

    async def update_preferences(
        self,
        *,
        user_id: str,
        request: MemoryPreferencesUpdateRequest,
    ) -> MemoryPreferencesUpdateResponse:
        """Save practice preferences only after explicit consent."""
        if not request.consent_to_save_preferences:
            raise PermissionError("Explicit consent_to_save_preferences=true is required.")
        current = await self.settings_repository.get(user_id)
        consent_state = UserConsentState(
            consent_to_practice_summary=current.consent_state.consent_to_practice_summary,
            consent_to_save_preferences=True,
            do_not_store_raw_messages=True,
            allow_sensitive_memory=False,
        )
        settings = await self.settings_repository.save(
            user_id=user_id,
            consent_state=consent_state,
            practice_preferences=request.practice_preferences,
        )
        return MemoryPreferencesUpdateResponse(
            user_id=user_id,
            consent_state=settings.consent_state,
            practice_preferences=settings.practice_preferences,
        )

    async def disable_preferences(
        self,
        user_id: str,
    ) -> MemoryPreferencesUpdateResponse:
        """Turn off long-term practice preferences without deleting all memory."""
        current = await self.settings_repository.get(user_id)
        consent_state = UserConsentState(
            consent_to_practice_summary=current.consent_state.consent_to_practice_summary,
            consent_to_save_preferences=False,
            do_not_store_raw_messages=True,
            allow_sensitive_memory=False,
        )
        settings = await self.settings_repository.save(
            user_id=user_id,
            consent_state=consent_state,
            practice_preferences=PracticePreferences(),
        )
        return MemoryPreferencesUpdateResponse(
            user_id=user_id,
            consent_state=settings.consent_state,
            practice_preferences=settings.practice_preferences,
        )

    async def update_practice_summary_consent(
        self,
        *,
        user_id: str,
        consent_to_practice_summary: bool,
    ) -> PracticeSummaryConsentUpdateResponse:
        """Enable or revoke future agent use of saved practice summaries."""
        current = await self.settings_repository.get(user_id)
        consent_state = UserConsentState(
            consent_to_practice_summary=consent_to_practice_summary,
            consent_to_save_preferences=current.consent_state.consent_to_save_preferences,
            do_not_store_raw_messages=True,
            allow_sensitive_memory=False,
        )
        settings = await self.settings_repository.save(
            user_id=user_id,
            consent_state=consent_state,
        )
        return PracticeSummaryConsentUpdateResponse(
            user_id=user_id,
            consent_state=settings.consent_state,
        )

    async def get_onboarding_profile(
        self,
        user_id: str,
    ) -> UserOnboardingProfileResponse:
        """Return low-sensitivity onboarding choices."""
        settings = await self.settings_repository.get(user_id)
        return UserOnboardingProfileResponse(
            user_id=user_id,
            onboarding_profile=settings.onboarding_profile,
        )

    async def update_onboarding_profile(
        self,
        *,
        user_id: str,
        onboarding_profile: UserOnboardingProfile,
    ) -> UserOnboardingProfileResponse:
        """Save low-sensitivity onboarding profile fields."""
        settings = await self.settings_repository.save(
            user_id=user_id,
            onboarding_profile=onboarding_profile,
        )
        return UserOnboardingProfileResponse(
            user_id=user_id,
            onboarding_profile=settings.onboarding_profile,
        )

    async def reset_onboarding_profile(
        self,
        user_id: str,
    ) -> UserOnboardingProfileResponse:
        """Reset onboarding profile choices while preserving other memory settings."""
        settings = await self.settings_repository.save(
            user_id=user_id,
            onboarding_profile=UserOnboardingProfile(),
        )
        return UserOnboardingProfileResponse(
            user_id=user_id,
            onboarding_profile=settings.onboarding_profile,
        )

memory_privacy_service = MemoryPrivacyService()
