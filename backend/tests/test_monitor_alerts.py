"""Tests for the deployment alert threshold helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_monitor_alerts_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "monitor_alerts.py"
    spec = importlib.util.spec_from_file_location("monitor_alerts", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_evaluate_alerts_reports_readiness_and_metric_thresholds() -> None:
    monitor_alerts = _load_monitor_alerts_module()

    alerts = monitor_alerts.evaluate_alerts(
        ready_ok=False,
        metrics={
            "crisis_runs": 3,
            "fallback_runs": 7,
            "rate_limit_hits": 4,
            "llm_concurrency_saturation": 1,
            "slow_request_count": 0,
            "latency_p95_ms": 1200.0,
        },
        thresholds=monitor_alerts.AlertThresholds(
            crisis_runs=2,
            fallback_runs=10,
            rate_limit_hits=3,
            llm_concurrency_saturation=0,
            slow_request_count=5,
            latency_p95_ms=1000.0,
        ),
    )

    assert "readiness check failed" in alerts
    assert "crisis runs exceeded threshold: 3 > 2" in alerts
    assert "rate limit hits exceeded threshold: 4 > 3" in alerts
    assert "LLM concurrency saturation exceeded threshold: 1 > 0" in alerts
    assert "p95 latency exceeded threshold: 1200.0ms > 1000.0ms" in alerts
    assert not any("fallback runs" in alert for alert in alerts)


def test_evaluate_alerts_allows_disabled_thresholds() -> None:
    monitor_alerts = _load_monitor_alerts_module()

    alerts = monitor_alerts.evaluate_alerts(
        ready_ok=True,
        metrics={
            "crisis_runs": 100,
            "fallback_runs": 100,
            "rate_limit_hits": 100,
            "llm_concurrency_saturation": 100,
            "slow_request_count": 100,
            "latency_p95_ms": 100000.0,
        },
        thresholds=monitor_alerts.AlertThresholds(
            crisis_runs=-1,
            fallback_runs=-1,
            rate_limit_hits=-1,
            llm_concurrency_saturation=-1,
            slow_request_count=-1,
            latency_p95_ms=-1,
        ),
    )

    assert alerts == []
