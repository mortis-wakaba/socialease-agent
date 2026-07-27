"""Contracts for typed module overlays on the shared conversation window."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models_conversation import ModuleType
from app.models_module_overlay import (
    ExposureOverlay,
    ModuleOverlay,
    RoleplayOverlay,
)


def test_module_overlay_rejects_payload_type_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        ModuleOverlay(
            conversation_id="conversation-1",
            user_id="owner",
            module_run_id="module-1",
            module_type=ModuleType.WORKSHEET,
            phase="active",
            payload=RoleplayOverlay(
                scenario_summary="小组讨论",
                difficulty=2,
            ),
            updated_at=datetime.now(UTC),
        )


def test_exposure_overlay_rejects_inverted_intensity_boundary() -> None:
    with pytest.raises(ValidationError, match="minimum intensity"):
        ExposureOverlay(
            minimum_intensity=6,
            maximum_intensity=3,
        )


def test_roleplay_overlay_contains_no_transcript_field() -> None:
    overlay = RoleplayOverlay(
        scenario_summary="课堂发言",
        difficulty=2,
        attempted_phrases=["我想补充一个观点"],
    )

    assert "messages" not in RoleplayOverlay.model_fields
    assert "transcript" not in RoleplayOverlay.model_fields
