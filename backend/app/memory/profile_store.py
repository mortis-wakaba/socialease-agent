"""User-profile summary store backed by a replaceable repository."""

from app.db.repositories import SQLiteUserProfileRepository, UserProfileRepository
from app.models_memory import UserPracticeSummary


class UserProfileStore:
    """Read privacy-minimized user practice summaries."""

    def __init__(self, repository: UserProfileRepository | None = None) -> None:
        self.repository = repository or SQLiteUserProfileRepository()

    def get_summary(self, user_id: str) -> UserPracticeSummary:
        """Return an aggregate summary for one user."""
        return self.repository.get_summary(user_id)


user_profile_store = UserProfileStore()
