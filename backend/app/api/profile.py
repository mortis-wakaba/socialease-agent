"""FastAPI routes for lightweight user-memory summaries."""

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_user, require_owner_path_user
from app.models_memory import (
    MemoryPreferencesUpdateRequest,
    MemoryPreferencesUpdateResponse,
    PracticeSummaryConsentUpdateRequest,
    PracticeSummaryConsentUpdateResponse,
    UserOnboardingProfileResponse,
    UserOnboardingProfileUpdateRequest,
    UserMemoryDeleteResponse,
    UserMemoryExportResponse,
    UserProfileResponse,
)
from app.models import RiskLevel
from app.models_session_review import (
    SessionReviewCreateRequest,
    SessionReviewCreateResponse,
    SessionReviewListResponse,
    SessionReviewRecord,
)
from app.observability.runtime_events import (
    record_memory_delete,
    record_memory_export,
    record_memory_preferences_disabled,
    record_memory_preferences_saved,
)
from app.db.factory import repository_factory
from app.privacy.persistence_gate import persistence_gate
from app.privacy.policy import PersistenceKind
from app.safety.classifier import RuleBasedSafetyClassifier
from app.safety.actions import HarnessAction
from app.safety.direct_actions import (
    PROTOCOL_HEADER_NAME,
    consume_direct_action_consent,
    require_direct_action_consent,
)
from app.services.memory_privacy_service import memory_privacy_service
from app.services.roleplay_service import roleplay_service
from app.services.support_resource_service import support_resource_service
from app.services.worksheet_service import worksheet_service

router = APIRouter(tags=["profile"])
session_review_repository = repository_factory().session_review_repository()
session_review_safety_classifier = RuleBasedSafetyClassifier()


@router.get("/users/{user_id}/profile", response_model=UserProfileResponse)
async def get_user_profile(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> UserProfileResponse:
    """Return a privacy-minimized practice summary for one user."""
    require_owner_path_user(user_id, current_user)
    return memory_privacy_service.profile(user_id)


@router.get("/users/{user_id}/memory/export", response_model=UserMemoryExportResponse)
async def export_user_memory(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> UserMemoryExportResponse:
    """Export user-owned memory records."""
    require_owner_path_user(user_id, current_user)
    response = memory_privacy_service.export(user_id)
    record_memory_export()
    return response


@router.delete("/users/{user_id}/memory", response_model=UserMemoryDeleteResponse)
async def delete_user_memory(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> UserMemoryDeleteResponse:
    """Delete user-owned memory records."""
    require_owner_path_user(user_id, current_user)
    response = memory_privacy_service.delete(user_id)
    await roleplay_service.delete_user_context(user_id)
    await worksheet_service.delete_user_context(user_id)
    await support_resource_service.delete_user_context(user_id)
    record_memory_delete()
    return response


@router.put(
    "/users/{user_id}/memory/preferences",
    response_model=MemoryPreferencesUpdateResponse,
)
async def update_memory_preferences(
    user_id: str,
    request: MemoryPreferencesUpdateRequest,
    current_user: AuthContext = Depends(get_current_user),
    protocol_id: str | None = Header(default=None, alias=PROTOCOL_HEADER_NAME),
) -> MemoryPreferencesUpdateResponse:
    """Save low-sensitivity practice preferences after explicit consent."""
    require_owner_path_user(user_id, current_user)
    consent = require_direct_action_consent(
        user_id=user_id,
        harness_action=HarnessAction.WRITE_MEMORY,
        payload={"user_id": user_id, **request.model_dump(mode="json")},
        protocol_id=protocol_id,
    )
    try:
        response = memory_privacy_service.update_preferences(
            user_id=user_id,
            request=request,
        )
        consume_direct_action_consent(
            user_id=user_id,
            consent=consent,
            result_summary="Updated memory preferences.",
        )
        record_memory_preferences_saved()
        return response
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))


@router.delete(
    "/users/{user_id}/memory/preferences",
    response_model=MemoryPreferencesUpdateResponse,
)
async def disable_memory_preferences(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> MemoryPreferencesUpdateResponse:
    """Turn off long-term practice preferences for one user."""
    require_owner_path_user(user_id, current_user)
    response = memory_privacy_service.disable_preferences(user_id)
    record_memory_preferences_disabled()
    return response


@router.put(
    "/users/{user_id}/memory/consent/practice-summary",
    response_model=PracticeSummaryConsentUpdateResponse,
)
async def update_practice_summary_consent(
    user_id: str,
    request: PracticeSummaryConsentUpdateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> PracticeSummaryConsentUpdateResponse:
    """Enable or revoke use of product practice summaries in future agent runs."""
    require_owner_path_user(user_id, current_user)
    return memory_privacy_service.update_practice_summary_consent(
        user_id=user_id,
        consent_to_practice_summary=request.consent_to_practice_summary,
    )


@router.get(
    "/users/{user_id}/onboarding",
    response_model=UserOnboardingProfileResponse,
)
async def get_onboarding_profile(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> UserOnboardingProfileResponse:
    """Return low-sensitivity onboarding profile choices."""
    require_owner_path_user(user_id, current_user)
    return memory_privacy_service.get_onboarding_profile(user_id)


@router.put(
    "/users/{user_id}/onboarding",
    response_model=UserOnboardingProfileResponse,
)
async def update_onboarding_profile(
    user_id: str,
    request: UserOnboardingProfileUpdateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> UserOnboardingProfileResponse:
    """Persist low-sensitivity onboarding profile choices."""
    require_owner_path_user(user_id, current_user)
    return memory_privacy_service.update_onboarding_profile(
        user_id=user_id,
        onboarding_profile=request.onboarding_profile,
    )


@router.delete(
    "/users/{user_id}/onboarding",
    response_model=UserOnboardingProfileResponse,
)
async def reset_onboarding_profile(
    user_id: str,
    current_user: AuthContext = Depends(get_current_user),
) -> UserOnboardingProfileResponse:
    """Reset low-sensitivity onboarding profile choices for one user."""
    require_owner_path_user(user_id, current_user)
    return memory_privacy_service.reset_onboarding_profile(user_id)


@router.post(
    "/users/{user_id}/session-reviews",
    response_model=SessionReviewCreateResponse,
)
async def create_session_review(
    user_id: str,
    request: SessionReviewCreateRequest,
    current_user: AuthContext = Depends(get_current_user),
) -> SessionReviewCreateResponse:
    """Save a low-sensitivity structured review after a practice session."""
    require_owner_path_user(user_id, current_user)
    safety_result = await session_review_safety_classifier.classify(request.next_step)
    if safety_result.risk_level == RiskLevel.CRISIS:
        return SessionReviewCreateResponse(
            review=None,
            saved=False,
            message=(
                "这条复盘里出现了安全风险表达，本次不保存为练习记录。"
                "请优先联系可信任的人、学校心理中心或当地紧急服务。"
            ),
        )
    if not request.save_record:
        return SessionReviewCreateResponse(
            review=None,
            saved=False,
            message="已完成本次复盘，没有保存为长期练习记录。",
        )
    record = SessionReviewRecord(
        user_id=user_id,
        source=request.source,
        source_id=request.source_id,
        completed=request.completed,
        anxiety_before=request.anxiety_before,
        anxiety_after=request.anxiety_after,
        next_step_summary=persistence_gate.persist_text(
            user_id=user_id,
            kind=PersistenceKind.SESSION_REVIEW_NEXT_STEP,
            text=request.next_step.strip(),
        ).persisted_text,
    )
    saved = session_review_repository.save(record)
    return SessionReviewCreateResponse(
        review=saved,
        saved=True,
        message="已保存低敏结构化复盘，可在历史和导出记录中查看或删除。",
    )


@router.get(
    "/users/{user_id}/session-reviews",
    response_model=SessionReviewListResponse,
)
async def list_session_reviews(
    user_id: str,
    limit: int = 20,
    current_user: AuthContext = Depends(get_current_user),
) -> SessionReviewListResponse:
    """Return recent low-sensitivity session reviews for one user."""
    require_owner_path_user(user_id, current_user)
    bounded_limit = min(max(limit, 1), 50)
    return SessionReviewListResponse(
        user_id=user_id,
        reviews=session_review_repository.list_for_user(user_id, bounded_limit),
    )
