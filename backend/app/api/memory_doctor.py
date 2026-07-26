"""Read-only owner-scoped Memory Doctor API."""

from fastapi import APIRouter, Depends

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user, require_owner_path_user
from app.models_memory_doctor import MemoryDoctorReport
from app.services.memory_doctor_service import memory_doctor_service


router = APIRouter(tags=["memory-doctor"])


@router.get(
    "/users/{user_id}/memory-doctor",
    response_model=MemoryDoctorReport,
)
async def diagnose_user_memory(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryDoctorReport:
    """Return a content-free read-only quality report for one owner."""
    require_owner_path_user(user_id, current_user)
    return memory_doctor_service.diagnose(user_id)
