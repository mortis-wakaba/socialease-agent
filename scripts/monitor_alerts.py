#!/usr/bin/env python3
"""Check SocialEase readiness and aggregate metrics, optionally sending alerts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AlertThresholds:
    """Operational thresholds for aggregate, non-identifying metrics."""

    crisis_runs: int
    fallback_runs: int
    rate_limit_hits: int
    llm_concurrency_saturation: int
    slow_request_count: int
    latency_p95_ms: float


def evaluate_alerts(
    *,
    ready_ok: bool,
    metrics: dict[str, Any] | None,
    thresholds: AlertThresholds,
) -> list[str]:
    """Return alert messages for readiness or metrics threshold violations."""
    alerts: list[str] = []
    if not ready_ok:
        alerts.append("readiness check failed")
    if metrics is None:
        alerts.append("metrics endpoint unavailable")
        return alerts

    checks = (
        ("crisis_runs", thresholds.crisis_runs, "crisis runs"),
        ("fallback_runs", thresholds.fallback_runs, "fallback runs"),
        ("rate_limit_hits", thresholds.rate_limit_hits, "rate limit hits"),
        (
            "llm_concurrency_saturation",
            thresholds.llm_concurrency_saturation,
            "LLM concurrency saturation",
        ),
        ("slow_request_count", thresholds.slow_request_count, "slow requests"),
    )
    for key, threshold, label in checks:
        value = int(metrics.get(key, 0) or 0)
        if threshold >= 0 and value > threshold:
            alerts.append(f"{label} exceeded threshold: {value} > {threshold}")

    p95 = float(metrics.get("latency_p95_ms", 0.0) or 0.0)
    if thresholds.latency_p95_ms >= 0 and p95 > thresholds.latency_p95_ms:
        alerts.append(
            f"p95 latency exceeded threshold: {p95:.1f}ms > {thresholds.latency_p95_ms:.1f}ms"
        )
    return alerts


def load_thresholds_from_env() -> AlertThresholds:
    """Load alert thresholds from environment variables."""
    return AlertThresholds(
        crisis_runs=int(os.getenv("SOCIALEASE_ALERT_CRISIS_RUNS", "5")),
        fallback_runs=int(os.getenv("SOCIALEASE_ALERT_FALLBACK_RUNS", "20")),
        rate_limit_hits=int(os.getenv("SOCIALEASE_ALERT_RATE_LIMIT_HITS", "20")),
        llm_concurrency_saturation=int(
            os.getenv("SOCIALEASE_ALERT_LLM_CONCURRENCY_SATURATION", "0")
        ),
        slow_request_count=int(os.getenv("SOCIALEASE_ALERT_SLOW_REQUESTS", "20")),
        latency_p95_ms=float(os.getenv("SOCIALEASE_ALERT_LATENCY_P95_MS", "5000")),
    )


def fetch_json(url: str, *, timeout_seconds: float) -> tuple[bool, dict[str, Any] | None]:
    """Fetch JSON and return success plus parsed payload."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return 200 <= response.status < 300, payload
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return False, None


def post_webhook(
    webhook_url: str,
    *,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> bool:
    """Send one generic JSON webhook alert."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return False


def main() -> int:
    """Run alert checks once."""
    parser = argparse.ArgumentParser(description="Check SocialEase readiness and metrics.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("SOCIALEASE_MONITOR_BASE_URL", "http://127.0.0.1:8000"),
        help="Backend base URL.",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.getenv("SOCIALEASE_ALERT_WEBHOOK_URL", ""),
        help="Optional generic JSON webhook URL.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("SOCIALEASE_ALERT_TIMEOUT_SECONDS", "5")),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fail-on-alerts",
        action="store_true",
        help="Return non-zero when alerts are found. Useful for CI gates.",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    ready_ok, ready_payload = fetch_json(
        f"{base_url}/ready",
        timeout_seconds=args.timeout_seconds,
    )
    metrics_ok, metrics_payload = fetch_json(
        f"{base_url}/api/harness/metrics?limit=100",
        timeout_seconds=args.timeout_seconds,
    )
    alerts = evaluate_alerts(
        ready_ok=ready_ok,
        metrics=metrics_payload if metrics_ok else None,
        thresholds=load_thresholds_from_env(),
    )
    output = {
        "service": "socialease-agent",
        "base_url": base_url,
        "alerts": alerts,
        "ready_status": ready_payload.get("status") if ready_payload else "unavailable",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    if not alerts:
        return 0
    if args.webhook_url and not args.dry_run:
        if not post_webhook(
            args.webhook_url,
            payload=output,
            timeout_seconds=args.timeout_seconds,
        ):
            print("Failed to send alert webhook.", file=sys.stderr)
            return 2
    return 1 if args.fail_on_alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
