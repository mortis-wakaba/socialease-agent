"""Tests for intent-independent response presentation constraints."""

from app.workflow.response_constraints import extract_response_constraints


def test_extracts_length_item_count_and_plain_language() -> None:
    constraints = extract_response_constraints(
        "请用三条建议回答，不超过80字，不要专业名词。"
    )

    assert constraints.item_count == 3
    assert constraints.max_chars == 80
    assert constraints.output_format == "steps"
    assert constraints.verbosity == "brief"
    assert constraints.plain_language is True


def test_format_request_does_not_encode_a_business_intent() -> None:
    constraints = extract_response_constraints("请用英文只回复一句话。")

    assert constraints.requested_language == "en"
    assert constraints.output_format == "single_sentence"
    assert constraints.item_count is None
