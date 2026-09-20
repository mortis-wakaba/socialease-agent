"""Build policy-approved sentence buffers and Server-Sent Events."""

import re

from app.models_conversation_api import (
    ConversationMessageResponse,
    ConversationStreamEvent,
)


class SentenceBuffer:
    """Hold provider deltas until a complete sentence is safe to inspect."""

    _BOUNDARY = re.compile(r"[。！？!?]\s*|\n+")

    def __init__(self, *, maximum_chars: int = 2400) -> None:
        self._pending = ""
        self._total_chars = 0
        self._maximum_chars = maximum_chars

    def feed(self, delta: str) -> list[str]:
        """Return newly completed sentences while retaining the trailing partial."""
        if not delta:
            return []
        self._total_chars += len(delta)
        if self._total_chars > self._maximum_chars:
            raise ValueError("streamed response exceeded the configured limit")
        self._pending += delta
        completed: list[str] = []
        cursor = 0
        for match in self._BOUNDARY.finditer(self._pending):
            completed.append(self._pending[cursor : match.end()])
            cursor = match.end()
        self._pending = self._pending[cursor:]
        return [item for item in completed if item]

    def flush(self) -> str:
        """Return the final partial sentence once provider generation completes."""
        pending = self._pending
        self._pending = ""
        return pending


def sentence_streaming_allowed(result: ConversationMessageResponse) -> bool:
    """Allow chunks only after the Harness records deterministic-only approval."""
    workflow = result.workflow_response
    if workflow is None:
        return False
    metadata = workflow.structured_data.get("output_guardrail")
    return (
        isinstance(metadata, dict)
        and metadata.get("evaluation_mode")
        in {"deterministic_only", "local_triage"}
        and metadata.get("action") == "allow"
    )


def sentence_chunks(response: str, *, maximum_chars: int = 240) -> list[str]:
    """Split validated text without exposing an unbounded partial sentence."""
    sentences = re.findall(r".*?(?:[。！？!?]\s*|\n+|$)", response, re.DOTALL)
    chunks: list[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        for start in range(0, len(sentence), maximum_chars):
            chunk = sentence[start : start + maximum_chars]
            if chunk:
                chunks.append(chunk)
    return chunks


def conversation_sse_event(payload: ConversationStreamEvent) -> str:
    """Serialize a typed payload under its matching SSE event name."""
    return f"event: {payload.type}\ndata: {payload.model_dump_json()}\n\n"
