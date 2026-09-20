"""Benchmark service-level conversation latency with per-stage spans."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from statistics import fmean
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.llm.factory import create_local_llm_client
from app.models_conversation import HISTORY_NOTICE_VERSION
from app.observability.turn_latency import ConversationTurnLatencyReport
from app.services.conversation_service import ConversationService
from app.services.conversation_runtime import build_conversation_service
from app.safety.classifier import create_safety_classifier
from app.tracing.logger import trace_logger
from app.workflow.default_hooks import create_default_hooks
from app.workflow.engine import AgentHarness
from app.workflow.router import LlmIntentRouter, RuleBasedIntentRouter


class LatencyDistribution(BaseModel):
    """Summary statistics for one stage, in milliseconds."""

    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(ge=0)
    average_ms: float = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    minimum_ms: float = Field(ge=0)
    maximum_ms: float = Field(ge=0)


class CounterDistribution(BaseModel):
    """Summary statistics for one numeric per-turn counter."""

    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(ge=0)
    average: float = Field(ge=0)
    p50: float = Field(ge=0)
    p95: float = Field(ge=0)
    minimum: float = Field(ge=0)
    maximum: float = Field(ge=0)


class ConversationLatencyBenchmarkReport(BaseModel):
    """Content-free aggregate latency report for comparable fresh turns."""

    model_config = ConfigDict(extra="forbid")

    scope: str = "conversation_service"
    routing_backend: str
    warmup_runs: int = Field(ge=0)
    measured_runs: int = Field(gt=0)
    total: LatencyDistribution
    first_output: LatencyDistribution
    stages: dict[str, LatencyDistribution]
    milestones: dict[str, LatencyDistribution]
    counters: dict[str, CounterDistribution]
    flag_rates: dict[str, float]
    stage_hierarchy: dict[str, list[str]]


class ConversationLatencySuiteReport(BaseModel):
    """Cold/first, warm serial, streaming, and queued concurrency scenarios."""

    model_config = ConfigDict(extra="forbid")

    routing_model: str
    generation_model: str
    true_model_cold_start_controlled: bool = False
    scenarios: dict[str, ConversationLatencyBenchmarkReport]


def summarize_latency_reports(
    reports: list[ConversationTurnLatencyReport],
    *,
    routing_backend: str,
    warmup_runs: int = 0,
) -> ConversationLatencyBenchmarkReport:
    """Aggregate request-local reports without retaining conversation content."""
    if not reports:
        raise ValueError("at least one latency report is required")
    stage_names = sorted(
        {stage for report in reports for stage in report.stages_ms}
    )
    milestone_names = sorted(
        {
            milestone
            for report in reports
            for milestone in report.milestones_ms
        }
    )
    counter_names = sorted(
        {counter for report in reports for counter in report.counters}
    )
    flag_names = sorted({flag for report in reports for flag in report.flags})
    return ConversationLatencyBenchmarkReport(
        routing_backend=routing_backend,
        warmup_runs=warmup_runs,
        measured_runs=len(reports),
        total=_distribution([report.total_ms for report in reports]),
        first_output=_distribution(
            [report.first_output_ms for report in reports]
        ),
        stages={
            stage: _distribution(
                [
                    report.stages_ms[stage]
                    for report in reports
                    if stage in report.stages_ms
                ]
            )
            for stage in stage_names
        },
        milestones={
            milestone: _distribution(
                [
                    report.milestones_ms[milestone]
                    for report in reports
                    if milestone in report.milestones_ms
                ]
            )
            for milestone in milestone_names
        },
        counters={
            counter: _counter_distribution(
                [
                    report.counters[counter]
                    for report in reports
                    if counter in report.counters
                ]
            )
            for counter in counter_names
        },
        flag_rates={
            flag: round(
                sum(flag in report.flags for report in reports) / len(reports),
                4,
            )
            for flag in flag_names
        },
        stage_hierarchy={
            "total": ["command_claim", "turn_execution", "command_complete"],
            "turn_execution": ["turn_preparation", "turn_dispatch"],
            "turn_preparation": [
                "state_load",
                "safety",
                "input_persistence",
            ],
            "turn_dispatch": [
                "routing",
                "permission",
                "permission_protocol",
                "active_memory",
                "context",
                "workflow",
                "response_persistence",
                "response_assembly",
            ],
            "workflow": [
                "workflow.safety",
                "workflow.routing",
                "workflow.skill",
                "workflow.output_guardrail",
                "workflow.trace",
            ],
            "tool_calling": ["tool_model", "tool_execution"],
            "output_guardrail": [
                "guardrail.semantic",
                "guardrail.repair_generation",
                "guardrail.repair_recheck",
            ],
            "provider_queueing": [
                "llm_queue_wait.local",
                "llm_queue_wait.api",
            ],
            "provider_http": [
                "llm_http.safety.total",
                "llm_http.routing.total",
                "llm_http.tool_model.total",
                "llm_http.guardrail.semantic.total",
            ],
        },
    )


async def run_conversation_latency_benchmark(
    *,
    measured_runs: int = 30,
    warmup_runs: int = 3,
    streaming_requested: bool = False,
) -> ConversationLatencyBenchmarkReport:
    """Run fresh, ordinary turns through the production service composition."""
    if measured_runs < 1 or warmup_runs < 0:
        raise ValueError("measured_runs must be positive and warmup_runs non-negative")
    captured: list[ConversationTurnLatencyReport] = []
    local_llm_client = create_local_llm_client()
    intent_router = (
        LlmIntentRouter(llm_client=local_llm_client)
        if local_llm_client is not None
        else RuleBasedIntentRouter()
    )
    service = build_conversation_service(
        harness=AgentHarness(
            trace_logger=trace_logger,
            hooks=create_default_hooks(),
            intent_router=intent_router,
        ),
        intent_router=intent_router,
        lightweight_llm_client=local_llm_client,
        latency_sink=captured.append,
    )
    benchmark_user_id = f"latency-eval-{uuid4().hex}"
    try:
        for index in range(warmup_runs + measured_runs):
            await _run_fresh_turn(
                service,
                user_id=benchmark_user_id,
                run_key=f"latency-eval-{index}",
                streaming_requested=streaming_requested,
            )
    finally:
        await service.close()
    measured = captured[warmup_runs:]
    return summarize_latency_reports(
        measured,
        routing_backend=(
            "local_llm" if local_llm_client is not None else "rule_based"
        ),
        warmup_runs=warmup_runs,
    )


async def run_conversation_latency_suite(
    *,
    warm_serial_runs: int = 20,
    warmup_runs: int = 3,
    streaming_runs: int = 10,
    concurrency: int = 4,
    concurrent_rounds: int = 2,
) -> ConversationLatencySuiteReport:
    """Run separated first-request, warm, streaming, and concurrency scenarios."""
    if min(warm_serial_runs, streaming_runs, concurrency, concurrent_rounds) < 1:
        raise ValueError("scenario run counts and concurrency must be positive")
    captured: list[ConversationTurnLatencyReport] = []
    local_llm_client = create_local_llm_client()
    intent_router = (
        LlmIntentRouter(llm_client=local_llm_client)
        if local_llm_client is not None
        else RuleBasedIntentRouter()
    )
    service = build_conversation_service(
        harness=AgentHarness(
            trace_logger=trace_logger,
            hooks=create_default_hooks(),
            intent_router=intent_router,
        ),
        intent_router=intent_router,
        lightweight_llm_client=local_llm_client,
        latency_sink=captured.append,
    )
    benchmark_user_id = f"latency-suite-{uuid4().hex}"
    routing_backend = (
        "local_llm" if local_llm_client is not None else "rule_based"
    )
    scenarios: dict[str, ConversationLatencyBenchmarkReport] = {}
    try:
        await _run_fresh_turn(
            service,
            user_id=benchmark_user_id,
            run_key="first-process-request",
            streaming_requested=False,
        )
        scenarios["first_process_request"] = summarize_latency_reports(
            list(captured),
            routing_backend=routing_backend,
        )

        captured.clear()
        for index in range(warmup_runs):
            await _run_fresh_turn(
                service,
                user_id=benchmark_user_id,
                run_key=f"warmup-{index}",
                streaming_requested=False,
            )
        captured.clear()
        for index in range(warm_serial_runs):
            await _run_fresh_turn(
                service,
                user_id=benchmark_user_id,
                run_key=f"warm-serial-{index}",
                streaming_requested=False,
            )
        scenarios["warm_serial_full_response"] = summarize_latency_reports(
            list(captured),
            routing_backend=routing_backend,
            warmup_runs=warmup_runs,
        )

        captured.clear()
        for index in range(streaming_runs):
            await _run_fresh_turn(
                service,
                user_id=benchmark_user_id,
                run_key=f"warm-stream-{index}",
                streaming_requested=True,
            )
        scenarios["warm_serial_streaming"] = summarize_latency_reports(
            list(captured),
            routing_backend=routing_backend,
            warmup_runs=warmup_runs,
        )

        captured.clear()
        for round_index in range(concurrent_rounds):
            await _run_concurrent_fresh_turns(
                service,
                user_id=benchmark_user_id,
                round_index=round_index,
                concurrency=concurrency,
            )
        scenarios["concurrent_full_response"] = summarize_latency_reports(
            list(captured),
            routing_backend=routing_backend,
            warmup_runs=warmup_runs,
        )
    finally:
        await service.close()
    return ConversationLatencySuiteReport(
        routing_model=(
            os.getenv("LOCAL_LLM_MODEL", "local_llm")
            if local_llm_client is not None
            else "rule_based"
        ),
        generation_model=os.getenv("LLM_MODEL", "disabled"),
        scenarios=scenarios,
    )


async def _run_fresh_turn(
    service: ConversationService,
    *,
    user_id: str,
    run_key: str,
    streaming_requested: bool,
) -> None:
    """Run one measured turn while excluding conversation setup and cleanup."""
    conversation = await service.create_conversation(
        user_id=user_id,
        title="Latency evaluation (demo)",
        history_notice_version=HISTORY_NOTICE_VERSION,
        history_notice_acknowledged=True,
    )

    async def discard_chunk(_text: str) -> None:
        return None

    try:
        await _send_existing_turn(
            service,
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            run_key=run_key,
            streaming_requested=streaming_requested,
            chunk_sink=discard_chunk,
        )
    finally:
        await service.delete_conversation(
            conversation_id=conversation.conversation_id,
            user_id=user_id,
        )


async def _run_concurrent_fresh_turns(
    service: ConversationService,
    *,
    user_id: str,
    round_index: int,
    concurrency: int,
) -> None:
    """Exclude setup/cleanup while overlapping only measured requests."""
    conversations = [
        await service.create_conversation(
            user_id=user_id,
            title="Concurrent latency evaluation (demo)",
            history_notice_version=HISTORY_NOTICE_VERSION,
            history_notice_acknowledged=True,
        )
        for _ in range(concurrency)
    ]

    async def discard_chunk(_text: str) -> None:
        return None

    try:
        await asyncio.gather(
            *(
                _send_existing_turn(
                    service,
                    conversation_id=conversation.conversation_id,
                    user_id=user_id,
                    run_key=f"concurrent-{round_index}-{slot}",
                    streaming_requested=False,
                    chunk_sink=discard_chunk,
                )
                for slot, conversation in enumerate(conversations)
            )
        )
    finally:
        for conversation in conversations:
            await service.delete_conversation(
                conversation_id=conversation.conversation_id,
                user_id=user_id,
            )


async def _send_existing_turn(
    service: ConversationService,
    *,
    conversation_id: str,
    user_id: str,
    run_key: str,
    streaming_requested: bool,
    chunk_sink: Callable[[str], Awaitable[None]],
) -> None:
    """Send one measured benchmark request against prepared state."""
    await service.send_message(
        conversation_id=conversation_id,
        user_id=user_id,
        message="今天有点紧张，想简单梳理一下感受。",
        idempotency_key=run_key,
        streaming_requested=streaming_requested,
        response_chunk_sink=(chunk_sink if streaming_requested else None),
    )


def _distribution(values: list[float]) -> LatencyDistribution:
    if not values:
        return LatencyDistribution(
            sample_count=0,
            average_ms=0.0,
            p50_ms=0.0,
            p95_ms=0.0,
            minimum_ms=0.0,
            maximum_ms=0.0,
        )
    return LatencyDistribution(
        sample_count=len(values),
        average_ms=round(fmean(values), 3),
        p50_ms=round(_percentile(values, 0.50), 3),
        p95_ms=round(_percentile(values, 0.95), 3),
        minimum_ms=round(min(values), 3),
        maximum_ms=round(max(values), 3),
    )


def _counter_distribution(values: list[float]) -> CounterDistribution:
    """Summarize per-turn counts without applying millisecond field names."""
    if not values:
        return CounterDistribution(
            sample_count=0,
            average=0.0,
            p50=0.0,
            p95=0.0,
            minimum=0.0,
            maximum=0.0,
        )
    return CounterDistribution(
        sample_count=len(values),
        average=round(fmean(values), 3),
        p50=round(_percentile(values, 0.50), 3),
        p95=round(_percentile(values, 0.95), 3),
        minimum=round(min(values), 3),
        maximum=round(max(values), 3),
    )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark conversation input-to-output latency by stage.",
    )
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--warmup-runs", type=int, default=3)
    parser.add_argument("--stream-runs", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--concurrent-rounds", type=int, default=2)
    parser.add_argument("--output", type=Path)
    return parser


async def _main() -> None:
    args = _build_parser().parse_args()
    report = await run_conversation_latency_suite(
        warm_serial_runs=args.runs,
        warmup_runs=args.warmup_runs,
        streaming_runs=args.stream_runs,
        concurrency=args.concurrency,
        concurrent_rounds=args.concurrent_rounds,
    )
    rendered = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    os.environ.setdefault("SOCIALEASE_AUTH_MODE", "demo")
    asyncio.run(_main())
