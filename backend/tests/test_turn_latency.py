"""Tests for request-local conversation latency measurement and summaries."""

from app.evals.conversation_latency import summarize_latency_reports
from app.observability.turn_latency import (
    ConversationTurnLatencyRecorder,
    mark_first_turn_output,
    mark_turn_flag,
    mark_turn_milestone,
    measure_turn_stage,
    record_turn_counter,
    reset_turn_latency,
    start_turn_latency,
)


def test_turn_latency_records_nested_stages_and_first_output() -> None:
    recorder, token = start_turn_latency(streaming_requested=True)
    try:
        with measure_turn_stage("turn_execution"):
            with measure_turn_stage("routing"):
                pass
            mark_turn_milestone("model_first_token")
            mark_turn_flag("routing_llm_used")
            record_turn_counter("llm_usage.routing.prompt_tokens", 12)
            mark_first_turn_output()
        report = recorder.finish()
    finally:
        reset_turn_latency(token)

    assert report.streaming_requested is True
    assert report.total_ms >= report.first_output_ms
    assert report.stages_ms["turn_execution"] >= report.stages_ms["routing"]
    assert report.milestones_ms["model_first_token"] <= report.first_output_ms
    assert report.counters["llm_usage.routing.prompt_tokens"] == 12
    assert report.flags == ("routing_llm_used",)


def test_turn_latency_context_is_reset_after_request() -> None:
    recorder, token = start_turn_latency(streaming_requested=False)
    reset_turn_latency(token)

    with measure_turn_stage("outside_request"):
        pass

    assert "outside_request" not in recorder.stages_ms


def test_latency_summary_calculates_average_and_nearest_rank_p95() -> None:
    reports = []
    for total_ms, routing_ms in ((10.0, 1.0), (20.0, 2.0), (50.0, 5.0)):
        recorder = ConversationTurnLatencyRecorder(streaming_requested=False)
        recorder.record("routing", routing_ms)
        recorder.mark_milestone("generation_complete")
        frozen = recorder.finish()
        reports.append(
            frozen.__class__(
                turn_id=frozen.turn_id,
                total_ms=total_ms,
                first_output_ms=total_ms,
                streaming_requested=False,
                stages_ms=frozen.stages_ms,
                milestones_ms=frozen.milestones_ms,
                counters={"llm_usage.routing.prompt_tokens": routing_ms * 10},
                flags=("routing_llm_used",),
            )
        )

    summary = summarize_latency_reports(
        reports,
        routing_backend="rule_based",
        warmup_runs=2,
    )

    assert summary.total.average_ms == 26.667
    assert summary.total.p50_ms == 20.0
    assert summary.total.p95_ms == 50.0
    assert summary.stages["routing"].average_ms == 2.667
    assert summary.milestones["generation_complete"].sample_count == 3
    assert summary.counters["llm_usage.routing.prompt_tokens"].average == 26.667
    assert summary.counters["llm_usage.routing.prompt_tokens"].p95 == 50.0
    assert summary.flag_rates["routing_llm_used"] == 1.0
    assert summary.warmup_runs == 2
