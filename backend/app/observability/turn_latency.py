"""Request-local latency spans for one conversation turn."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from time import perf_counter
from uuid import uuid4


@dataclass(frozen=True)
class ConversationTurnLatencyReport:
    """Content-free timing report for one completed conversation command."""

    turn_id: str
    total_ms: float
    first_output_ms: float
    streaming_requested: bool
    stages_ms: dict[str, float]
    milestones_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, float] = field(default_factory=dict)
    flags: tuple[str, ...] = ()


ConversationTurnLatencySink = Callable[[ConversationTurnLatencyReport], None]


@dataclass
class ConversationTurnLatencyRecorder:
    """Accumulate nested, content-free spans for one logical turn."""

    streaming_requested: bool
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    stages_ms: dict[str, float] = field(default_factory=dict)
    milestones_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, float] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    _started: float = field(default_factory=perf_counter)
    _first_output_ms: float | None = None

    def record(self, stage: str, latency_ms: float) -> None:
        """Add elapsed time to a stage that may execute more than once."""
        self.stages_ms[stage] = self.stages_ms.get(stage, 0.0) + latency_ms

    def mark_first_output(self) -> None:
        """Capture time to first stream chunk, or later the final JSON output."""
        if self._first_output_ms is None:
            self._first_output_ms = (perf_counter() - self._started) * 1000

    def mark_milestone(self, milestone: str) -> None:
        """Capture the first occurrence of a request-relative milestone."""
        if milestone not in self.milestones_ms:
            self.milestones_ms[milestone] = (
                perf_counter() - self._started
            ) * 1000

    def increment(self, counter: str, value: float) -> None:
        """Add a non-sensitive numeric provider or workflow counter."""
        self.counters[counter] = self.counters.get(counter, 0.0) + value

    def finish(self) -> ConversationTurnLatencyReport:
        """Freeze the current measurements into a serializable report."""
        total_ms = (perf_counter() - self._started) * 1000
        first_output_ms = self._first_output_ms
        if first_output_ms is None:
            first_output_ms = total_ms
        return ConversationTurnLatencyReport(
            turn_id=self.turn_id,
            total_ms=round(total_ms, 3),
            first_output_ms=round(first_output_ms, 3),
            streaming_requested=self.streaming_requested,
            stages_ms={
                stage: round(latency_ms, 3)
                for stage, latency_ms in sorted(self.stages_ms.items())
            },
            milestones_ms={
                milestone: round(latency_ms, 3)
                for milestone, latency_ms in sorted(self.milestones_ms.items())
            },
            counters={
                counter: round(value, 3)
                for counter, value in sorted(self.counters.items())
            },
            flags=tuple(sorted(self.flags)),
        )


_current_recorder: ContextVar[ConversationTurnLatencyRecorder | None] = ContextVar(
    "conversation_turn_latency_recorder",
    default=None,
)
_current_stage_stack: ContextVar[tuple[str, ...]] = ContextVar(
    "conversation_turn_latency_stage_stack",
    default=(),
)


def start_turn_latency(
    *,
    streaming_requested: bool,
) -> tuple[ConversationTurnLatencyRecorder, Token[ConversationTurnLatencyRecorder | None]]:
    """Install a request-local recorder and return its reset token."""
    recorder = ConversationTurnLatencyRecorder(
        streaming_requested=streaming_requested,
    )
    return recorder, _current_recorder.set(recorder)


def reset_turn_latency(
    token: Token[ConversationTurnLatencyRecorder | None],
) -> None:
    """Restore the previous recorder after a turn completes."""
    _current_recorder.reset(token)


@contextmanager
def measure_turn_stage(stage: str) -> Iterator[None]:
    """Measure a synchronous or asynchronous code block when tracing is active."""
    recorder = _current_recorder.get()
    if recorder is None:
        yield
        return
    stage_token = _current_stage_stack.set((*_current_stage_stack.get(), stage))
    started = perf_counter()
    try:
        yield
    finally:
        recorder.record(stage, (perf_counter() - started) * 1000)
        _current_stage_stack.reset(stage_token)


def record_turn_stage(stage: str, latency_ms: float) -> None:
    """Record a duration already measured by an existing workflow timer."""
    recorder = _current_recorder.get()
    if recorder is not None:
        recorder.record(stage, latency_ms)


def record_turn_counter(counter: str, value: float) -> None:
    """Add one request-local numeric counter when tracing is active."""
    recorder = _current_recorder.get()
    if recorder is not None and value >= 0:
        recorder.increment(counter, value)


def current_turn_stage() -> str | None:
    """Return the innermost measured stage for provider metric attribution."""
    stack = _current_stage_stack.get()
    return stack[-1] if stack else None


def mark_first_turn_output() -> None:
    """Mark the first response bytes produced by the active turn."""
    recorder = _current_recorder.get()
    if recorder is not None:
        recorder.mark_first_output()


def mark_turn_milestone(milestone: str) -> None:
    """Mark a named milestone on the active request-local recorder."""
    recorder = _current_recorder.get()
    if recorder is not None:
        recorder.mark_milestone(milestone)


def mark_turn_flag(flag: str) -> None:
    """Record one content-free outcome flag for benchmark rate calculations."""
    recorder = _current_recorder.get()
    if recorder is not None:
        recorder.flags.add(flag)
