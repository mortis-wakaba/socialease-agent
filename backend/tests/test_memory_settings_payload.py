"""Regression tests for memory settings schema-evolution payload loading."""

from app.memory.settings_payload import load_user_memory_settings_payload


def test_legacy_feedback_style_labels_are_normalized() -> None:
    """Common old Chinese labels should survive small punctuation differences."""
    cases = {
        " 温和、具体、可执行 ": "gentle_specific",
        "简短，可执行": "brief_actionable",
        "鼓励反思型": "encouraging_reflective",
        "鼓励式 ／ 带一点反思": "encouraging_reflective",
    }
    for raw, expected in cases.items():
        settings = load_user_memory_settings_payload(
            {"practice_preferences": {"preferred_feedback_style": raw}}
        )
        assert settings.practice_preferences.preferred_feedback_style == expected


def test_unknown_feedback_style_free_text_is_dropped() -> None:
    """Unknown free text must not become long-term memory."""
    settings = load_user_memory_settings_payload(
        {"practice_preferences": {"preferred_feedback_style": "我的手机号是13912345678"}}
    )

    assert settings.practice_preferences.preferred_feedback_style is None
