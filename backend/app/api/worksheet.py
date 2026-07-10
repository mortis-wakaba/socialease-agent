"""FastAPI routes for CBT-style self-reflection worksheets."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_current_user,
    hide_if_not_owner,
    resolve_request_user_id,
)
from app.models_worksheet import (
    WorksheetCreateRequest,
    WorksheetCreateResponse,
    WorksheetRecord,
)
from app.services.errors import ServiceNotFoundError
from app.services.worksheet_service import worksheet_service

router = APIRouter(prefix="/worksheet", tags=["worksheet"])


@router.post("/create", response_model=WorksheetCreateResponse)
async def create_worksheet(
    request: WorksheetCreateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> WorksheetCreateResponse:
    """Create a CBT-style worksheet from a user message."""
    effective_request = request.model_copy(
        update={"user_id": resolve_request_user_id(request.user_id, current_user)}
    )
    return await worksheet_service.create_worksheet(effective_request)


@router.get("/{worksheet_id}", response_model=WorksheetRecord)
async def get_worksheet(
    worksheet_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> WorksheetRecord:
    """Return a saved worksheet by id."""
    try:
        worksheet = worksheet_service.get_worksheet(worksheet_id)
        hide_if_not_owner(worksheet.user_id, current_user)
        return worksheet
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Worksheet not found")
