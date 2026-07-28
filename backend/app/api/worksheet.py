"""FastAPI routes for CBT-style self-reflection worksheets."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user, hide_if_not_owner
from app.models_worksheet import (
    WorksheetRecord,
)
from app.services.errors import ServiceNotFoundError
from app.services.worksheet_service import worksheet_service

router = APIRouter(prefix="/worksheet", tags=["worksheet"])


@router.get("/{worksheet_id}", response_model=WorksheetRecord)
async def get_worksheet(
    worksheet_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> WorksheetRecord:
    """Return a saved worksheet by id."""
    try:
        worksheet = await worksheet_service.get_worksheet(worksheet_id)
        hide_if_not_owner(worksheet.user_id, current_user)
        return worksheet
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail="Worksheet not found")
