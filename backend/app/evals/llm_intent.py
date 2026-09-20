"""Benchmark an OpenAI-compatible model on SocialEase intent routing."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from app.evals.loader import DATA_DIR, load_jsonl
from app.llm.openai_compatible import OpenAICompatibleLLMClient
from app.llm.prompts import (
    build_intent_router_system_prompt,
    build_intent_router_user_prompt,
)
from app.llm.retry import RetryPolicy
from app.models import Intent, IntentResult
from app.workflow.router import (
    INTENT_ROUTER_SCHEMA_NAME,
    apply_llm_intent_policy,
    build_intent_router_json_schema,
    compile_llm_intent_prediction,
)


SPECIALIZED_INTENTS = {
    Intent.ROLEPLAY_PRACTICE,
    Intent.CBT_WORKSHEET,
    Intent.EXPOSURE_PLANNING,
    Intent.CAMPUS_RESOURCE_QUERY,
    Intent.PROGRESS_REVIEW,
    Intent.CALENDAR_PLANNING,
}


class LLMIntentEvalCase(BaseModel):
    """One synthetic, non-crisis semantic-routing expectation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    message: str
    expected_intent: Intent
    demo: Literal[True]
    split: Literal["dev", "heldout"] = "dev"


class LLMIntentCaseResult(BaseModel):
    """Content-free per-case result safe to persist as an eval artifact."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    split: Literal["dev", "heldout"]
    category: str
    expected_intent: Intent
    raw_prediction: Intent | None = None
    routed_prediction: Intent | None = None
    confidence: float | None = None
    json_valid: bool
    schema_valid: bool
    latency_ms: float
    error_category: str | None = None


class OllamaRuntimeEvidence(BaseModel):
    """Device evidence reported by Ollama's running-model endpoint."""

    model_config = ConfigDict(extra="forbid")

    available: bool = False
    processor: Literal["gpu", "cpu", "mixed", "not_loaded", "unknown"] = "unknown"
    size_bytes: int | None = None
    size_vram_bytes: int | None = None
    vram_fraction: float | None = None


class LLMIntentMetrics(BaseModel):
    """Aggregate quality and latency metrics for one dataset partition."""

    model_config = ConfigDict(extra="forbid")

    dataset_size: int
    raw_accuracy: float
    raw_macro_f1: float
    routed_accuracy: float
    routed_macro_f1: float
    json_valid_rate: float
    schema_valid_rate: float
    specialized_false_positive_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    per_intent_routed_f1: dict[str, float]


class LLMIntentBenchmarkReport(BaseModel):
    """Aggregate model-quality, reliability, latency, and device metrics."""

    model_config = ConfigDict(extra="forbid")

    model: str
    base_url: str
    dataset_size: int
    raw_accuracy: float
    raw_macro_f1: float
    routed_accuracy: float
    routed_macro_f1: float
    json_valid_rate: float
    schema_valid_rate: float
    specialized_false_positive_rate: float
    first_request_latency_ms: float
    warm_latency_p50_ms: float
    warm_latency_p95_ms: float
    per_intent_routed_f1: dict[str, float]
    split_metrics: dict[str, LLMIntentMetrics]
    runtime: OllamaRuntimeEvidence
    cases: list[LLMIntentCaseResult]


async def run_llm_intent_benchmark(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float = 120.0,
    cold_start: bool = True,
    cases: list[LLMIntentEvalCase] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> LLMIntentBenchmarkReport:
    """Run sequential intent requests and return reproducible aggregate metrics."""
    resolved_cases = cases or [
        *load_jsonl(DATA_DIR / "intent_llm.jsonl", LLMIntentEvalCase),
        *load_jsonl(DATA_DIR / "intent_llm_heldout.jsonl", LLMIntentEvalCase),
    ]
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
    try:
        if cold_start:
            await _unload_ollama_model(client, base_url=base_url, model=model)
        llm_client = OpenAICompatibleLLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            json_mode_enabled=True,
            reasoning_effort="none",
            http_client=client,
            retry_policy=RetryPolicy(max_attempts=1),
        )
        outcomes = [
            await _evaluate_case(llm_client, case)
            for case in resolved_cases
        ]
        runtime = await _probe_ollama_runtime(
            client,
            base_url=base_url,
            model=model,
        )
    finally:
        if owns_client:
            await client.aclose()

    aggregate = summarize_outcomes(resolved_cases, outcomes)
    latencies = [outcome.latency_ms for outcome in outcomes]
    warm_latencies = latencies[1:] or latencies
    split_metrics = {
        split: summarize_outcomes(
            [case for case in resolved_cases if case.split == split],
            [outcome for outcome in outcomes if outcome.split == split],
        )
        for split in ("dev", "heldout")
        if any(case.split == split for case in resolved_cases)
    }
    return LLMIntentBenchmarkReport(
        model=model,
        base_url=base_url,
        dataset_size=len(outcomes),
        raw_accuracy=aggregate.raw_accuracy,
        raw_macro_f1=aggregate.raw_macro_f1,
        routed_accuracy=aggregate.routed_accuracy,
        routed_macro_f1=aggregate.routed_macro_f1,
        json_valid_rate=aggregate.json_valid_rate,
        schema_valid_rate=aggregate.schema_valid_rate,
        specialized_false_positive_rate=aggregate.specialized_false_positive_rate,
        first_request_latency_ms=latencies[0] if latencies else 0.0,
        warm_latency_p50_ms=_percentile(warm_latencies, 0.50),
        warm_latency_p95_ms=_percentile(warm_latencies, 0.95),
        per_intent_routed_f1=aggregate.per_intent_routed_f1,
        split_metrics=split_metrics,
        runtime=runtime,
        cases=outcomes,
    )


async def _evaluate_case(
    client: OpenAICompatibleLLMClient,
    case: LLMIntentEvalCase,
) -> LLMIntentCaseResult:
    started = perf_counter()
    raw_response: str | None = None
    error_category: str | None = None
    try:
        json_schema, max_tokens = build_intent_router_json_schema(case.message)
        raw_response = await client.generate_structured_json_text(
            system_prompt=build_intent_router_system_prompt(),
            user_prompt=build_intent_router_user_prompt(case.message),
            json_schema=json_schema,
            schema_name=INTENT_ROUTER_SCHEMA_NAME,
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # The benchmark records provider failures per case.
        error_category = type(exc).__name__
    latency_ms = round((perf_counter() - started) * 1000, 3)

    payload: object | None = None
    json_valid = False
    if raw_response is not None:
        try:
            payload = json.loads(raw_response)
            json_valid = True
        except json.JSONDecodeError:
            error_category = "INVALID_JSON"

    validated: IntentResult | None = None
    if json_valid:
        try:
            validated = compile_llm_intent_prediction(payload)
        except ValueError:
            error_category = "INVALID_SCHEMA"

    routed = apply_llm_intent_policy(validated) if validated is not None else None
    return LLMIntentCaseResult(
        case_id=case.id,
        split=case.split,
        category=case.category,
        expected_intent=case.expected_intent,
        raw_prediction=validated.intent if validated is not None else None,
        routed_prediction=routed.intent if routed is not None else None,
        confidence=validated.confidence if validated is not None else None,
        json_valid=json_valid,
        schema_valid=validated is not None,
        latency_ms=latency_ms,
        error_category=error_category,
    )


def summarize_outcomes(
    cases: list[LLMIntentEvalCase],
    outcomes: list[LLMIntentCaseResult],
) -> LLMIntentMetrics:
    """Summarize one aligned case partition without retaining message content."""
    if len(cases) != len(outcomes):
        raise ValueError("cases and outcomes lengths differ")
    expected = [case.expected_intent for case in cases]
    raw = [outcome.raw_prediction for outcome in outcomes]
    routed = [outcome.routed_prediction for outcome in outcomes]
    raw_accuracy, raw_macro_f1, _ = classification_metrics(expected, raw)
    routed_accuracy, routed_macro_f1, routed_f1 = classification_metrics(
        expected,
        routed,
    )
    negative_outcomes = [
        outcome
        for outcome in outcomes
        if outcome.expected_intent not in SPECIALIZED_INTENTS
    ]
    false_specialized = sum(
        outcome.routed_prediction in SPECIALIZED_INTENTS
        for outcome in negative_outcomes
    )
    latencies = [outcome.latency_ms for outcome in outcomes]
    return LLMIntentMetrics(
        dataset_size=len(outcomes),
        raw_accuracy=raw_accuracy,
        raw_macro_f1=raw_macro_f1,
        routed_accuracy=routed_accuracy,
        routed_macro_f1=routed_macro_f1,
        json_valid_rate=_ratio(sum(item.json_valid for item in outcomes), len(outcomes)),
        schema_valid_rate=_ratio(
            sum(item.schema_valid for item in outcomes), len(outcomes)
        ),
        specialized_false_positive_rate=_ratio(
            false_specialized, len(negative_outcomes)
        ),
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        per_intent_routed_f1=routed_f1,
    )


def classification_metrics(
    expected: list[Intent],
    predicted: list[Intent | None],
) -> tuple[float, float, dict[str, float]]:
    """Return accuracy, macro-F1, and per-label F1, counting invalids as misses."""
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted lengths differ")
    labels = sorted(set(expected), key=lambda item: item.value)
    per_label: dict[str, float] = {}
    for label in labels:
        true_positive = sum(
            actual == label and guess == label
            for actual, guess in zip(expected, predicted, strict=True)
        )
        false_positive = sum(
            actual != label and guess == label
            for actual, guess in zip(expected, predicted, strict=True)
        )
        false_negative = sum(
            actual == label and guess != label
            for actual, guess in zip(expected, predicted, strict=True)
        )
        denominator = (2 * true_positive) + false_positive + false_negative
        per_label[label.value] = (
            (2 * true_positive) / denominator if denominator else 0.0
        )
    correct = sum(
        actual == guess
        for actual, guess in zip(expected, predicted, strict=True)
    )
    accuracy = _ratio(correct, len(expected))
    macro_f1 = _ratio(sum(per_label.values()), len(per_label))
    return accuracy, macro_f1, per_label


async def _unload_ollama_model(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
) -> None:
    """Best-effort unload for a meaningful first-request latency measurement."""
    try:
        await client.post(
            _ollama_url(base_url, "/api/generate"),
            json={"model": model, "keep_alive": 0},
        )
    except httpx.HTTPError:
        return


async def _probe_ollama_runtime(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
) -> OllamaRuntimeEvidence:
    """Best-effort query of Ollama's content-free GPU allocation metadata."""
    try:
        response = await client.get(_ollama_url(base_url, "/api/ps"))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return OllamaRuntimeEvidence()
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return OllamaRuntimeEvidence(available=True)
    running = next(
        (
            item
            for item in models
            if isinstance(item, dict)
            and (item.get("name") == model or item.get("model") == model)
        ),
        None,
    )
    if running is None:
        return OllamaRuntimeEvidence(available=True, processor="not_loaded")
    size = _non_negative_int(running.get("size"))
    size_vram = _non_negative_int(running.get("size_vram"))
    fraction = (size_vram / size) if size and size_vram is not None else None
    if size_vram is None:
        processor = "unknown"
    elif size_vram == 0:
        processor = "cpu"
    elif size and size_vram >= size * 0.95:
        processor = "gpu"
    else:
        processor = "mixed"
    return OllamaRuntimeEvidence(
        available=True,
        processor=processor,
        size_bytes=size,
        size_vram_bytes=size_vram,
        vram_fraction=round(fraction, 4) if fraction is not None else None,
    )


def _ollama_url(base_url: str, path: str) -> str:
    url = httpx.URL(base_url)
    return str(url.copy_with(path=path, query=None))


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _ratio(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark a local or hosted model on SocialEase intent routing.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
    )
    parser.add_argument("--api-key", default=os.getenv("LOCAL_LLM_API_KEY", "ollama"))
    parser.add_argument("--model", default=os.getenv("LOCAL_LLM_MODEL", "qwen3.5:4b"))
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--cold-start",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    """Run the benchmark and optionally persist its content-free report."""
    args = _build_parser().parse_args()
    report = asyncio.run(
        run_llm_intent_benchmark(
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            cold_start=args.cold_start,
        )
    )
    rendered = report.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
