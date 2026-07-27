"""Domain-service contracts used only through unified conversation adapters."""

from pathlib import Path
from datetime import UTC, datetime

import pytest

from app.conversation.adapters.exposure import ExposureModuleAdapter
from app.models_exposure import (
    ExposureCompleteRequest,
    ExposureFeedbackStatus,
    ExposurePlanRequest,
)
from app.models_conversation import ExposureParameters, ModuleRun, ModuleType
from app.models_conversation_context import (
    ConversationContextDiagnostics,
    ConversationWorkingContext,
)
from app.models_support import SupportQueryRequest
from app.models_worksheet import (
    WorksheetCreateRequest,
    WorksheetSupplementRequest,
)
from app.services.exposure_service import ExposureService
from app.services.support_resource_service import SupportResourceService
from app.services.worksheet_service import WorksheetService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SOCIALEASE_DB_PATH", str(tmp_path / "modules.db"))
    monkeypatch.delenv("SOCIALEASE_DATABASE_URL", raising=False)
    monkeypatch.delenv("SOCIALEASE_REDIS_URL", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")


@pytest.mark.anyio
async def test_worksheet_service_creates_and_supplements_one_record() -> None:
    service = WorksheetService()
    created = await service.create_worksheet(
        WorksheetCreateRequest(
            user_id="owner",
            message="情境：课堂发言。情绪：紧张。强度：6。",
        )
    )
    assert created.worksheet is not None

    updated = await service.supplement_worksheet(
        WorksheetSupplementRequest(
            worksheet_id=created.worksheet.worksheet_id,
            user_id="owner",
            message="下一步：先写一句开场。",
        )
    )

    assert updated.worksheet is not None
    assert updated.worksheet.worksheet_id == created.worksheet.worksheet_id
    assert updated.worksheet.fields.next_action == "先写一句开场"


@pytest.mark.anyio
async def test_resource_service_returns_grounded_results_and_safe_unknown() -> None:
    service = SupportResourceService()
    grounded = await service.query_resources(
        SupportQueryRequest(
            user_id="owner",
            query="social anxiety CBT self-help public resource",
        )
    )
    unknown = await service.query_resources(
        SupportQueryRequest(user_id="owner", query="火星土壤采样 轨道力学")
    )

    assert grounded.blocked is False
    assert grounded.citations
    assert all(item.source_type == "external_public" for item in grounded.citations)
    assert unknown.unknown is True
    assert unknown.citations == []


@pytest.mark.anyio
async def test_exposure_service_requires_permission_before_increasing() -> None:
    service = ExposureService()
    created = await service.create_plan(
        ExposurePlanRequest(
            user_id="owner",
            target_scenario="课堂发言",
            current_anxiety_level=7,
        )
    )
    assert created.plan is not None
    task = created.plan.tasks[2]

    held = await service.complete_task(
        ExposureCompleteRequest(
            user_id="owner",
            task_id=task.task_id,
            status=ExposureFeedbackStatus.COMPLETED,
            anxiety_before=7,
            anxiety_after=4,
            reflection="完成了，但先保持强度。",
        )
    )

    assert held.next_task is not None
    assert held.next_task.difficulty <= task.difficulty
    assert "without explicit permission" in held.adjustment_reason


@pytest.mark.anyio
async def test_exposure_adapter_records_feedback_inside_unified_context() -> None:
    service = ExposureService()
    adapter = ExposureModuleAdapter(service)
    run = ModuleRun(
        module_run_id="exposure-run",
        conversation_id="conversation-1",
        user_id="owner",
        module_type=ModuleType.EXPOSURE,
        depth=1,
        module_parameters=ExposureParameters(
            goal="课堂发言",
            starting_anxiety=7,
        ),
        started_at=datetime.now(UTC),
    )
    context = ConversationWorkingContext(
        conversation_id=run.conversation_id,
        current_user_message="开始",
        active_module_stack=[run],
        diagnostics=ConversationContextDiagnostics(
            conversation_id_hash="0" * 16,
            recent_event_count=0,
            active_module_count=1,
            selected_memory_count=0,
            estimated_tokens=0,
            total_token_budget=7000,
            tokenizer_backend="test",
        ),
    )
    started = await adapter.start(run, context)
    run = run.model_copy(update={"domain_session_id": started.domain_session_id})
    overlay = await adapter.build_overlay(run, context)

    result = await adapter.handle_message(
        run,
        "完成了，前 7，后 4，先保持当前难度。",
        context,
        overlay,
    )

    plan = service.store.get_for_user("owner")
    assert plan is not None
    assert len(plan.attempts) == 1
    assert result.event_payload.permission_to_increase is False
    assert "不会自动提高强度" in result.response


@pytest.mark.anyio
async def test_domain_services_keep_crisis_out_of_ordinary_module_actions() -> None:
    worksheet = await WorksheetService().create_worksheet(
        WorksheetCreateRequest(user_id="owner", message="我不想活了，想伤害自己。")
    )
    exposure = await ExposureService().create_plan(
        ExposurePlanRequest(
            user_id="owner",
            target_scenario="我不想活了，想伤害自己。",
            current_anxiety_level=9,
        )
    )
    resource = await SupportResourceService().query_resources(
        SupportQueryRequest(user_id="owner", query="我不想活了，想伤害自己。")
    )

    assert worksheet.blocked is True
    assert exposure.blocked is True
    assert resource.blocked is True
