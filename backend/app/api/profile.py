"""FastAPI routes for lightweight user-memory summaries."""

from fastapi import APIRouter

from app.memory.profile_store import user_profile_store
from app.models_memory import UserProfileResponse

router = APIRouter(tags=["profile"])


@router.get("/users/{user_id}/profile", response_model=UserProfileResponse)
async def get_user_profile(user_id: str) -> UserProfileResponse:
    """Return a privacy-minimized practice summary for one user."""
    return UserProfileResponse(
        user_id=user_id,
        practice_summary=user_profile_store.get_summary(user_id),
    )
