"""Tests for policy-approved sentence-buffered response delivery."""

from app.models_conversation_api import (
    ConversationStreamChunkEvent,
    ConversationStreamErrorEvent,
)
from app.services.response_streaming import (
    SentenceBuffer,
    conversation_sse_event,
    sentence_chunks,
)


def test_sentence_buffer_holds_partial_provider_deltas() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("先试着说") == []
    assert buffer.feed("一句。下一") == ["先试着说一句。"]
    assert buffer.feed("句！尾巴") == ["下一句！"]
    assert buffer.flush() == "尾巴"


def test_sentence_chunks_preserve_text_and_split_chinese_punctuation() -> None:
    response = "可以先准备一句开场。然后决定是否继续！\n也可以随时暂停。"

    chunks = sentence_chunks(response)

    assert chunks == [
        "可以先准备一句开场。",
        "然后决定是否继续！\n",
        "也可以随时暂停。",
    ]
    assert "".join(chunks) == response


def test_sentence_chunks_bound_long_unpunctuated_output() -> None:
    response = "练" * 501

    chunks = sentence_chunks(response, maximum_chars=240)

    assert [len(chunk) for chunk in chunks] == [240, 240, 21]
    assert "".join(chunks) == response


def test_typed_sse_payload_owns_the_matching_event_name() -> None:
    chunk = conversation_sse_event(ConversationStreamChunkEvent(text="安全句。"))
    error = conversation_sse_event(
        ConversationStreamErrorEvent(detail="The response could not be completed")
    )

    assert chunk.startswith("event: response_chunk\n")
    assert '"type":"response_chunk"' in chunk
    assert error.startswith("event: response_error\n")
    assert '"type":"response_error"' in error
