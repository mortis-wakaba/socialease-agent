#!/usr/bin/env python3
"""Run deployment smoke checks against a SocialEase environment."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    """One smoke-check result."""

    name: str
    ok: bool
    detail: str


def fetch(url: str, *, timeout_seconds: float) -> tuple[int, str]:
    """Return response status and body text for one URL."""
    with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
        return response.status, response.read().decode("utf-8")


def check_json_status(
    *,
    name: str,
    url: str,
    expected_status: int,
    timeout_seconds: float,
) -> CheckResult:
    """Check one JSON endpoint status."""
    try:
        status, body = fetch(url, timeout_seconds=timeout_seconds)
        json.loads(body)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return CheckResult(name=name, ok=False, detail=exc.__class__.__name__)
    return CheckResult(
        name=name,
        ok=status == expected_status,
        detail=f"status={status}",
    )


def check_page(
    *,
    name: str,
    url: str,
    timeout_seconds: float,
) -> CheckResult:
    """Check that one frontend page returns a 2xx/3xx response."""
    try:
        status, _ = fetch(url, timeout_seconds=timeout_seconds)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        return CheckResult(name=name, ok=False, detail=exc.__class__.__name__)
    return CheckResult(name=name, ok=200 <= status < 400, detail=f"status={status}")


def main() -> int:
    """Run smoke checks once."""
    parser = argparse.ArgumentParser(description="Run SocialEase deployment smoke checks.")
    parser.add_argument(
        "--api-url",
        default=os.getenv("SOCIALEASE_MONITOR_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--frontend-url",
        default=os.getenv("SOCIALEASE_SMOKE_FRONTEND_URL", ""),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("SOCIALEASE_SMOKE_TIMEOUT_SECONDS", "5")),
    )
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    results = [
        check_json_status(
            name="health",
            url=f"{api_url}/health",
            expected_status=200,
            timeout_seconds=args.timeout_seconds,
        ),
        check_json_status(
            name="ready",
            url=f"{api_url}/ready",
            expected_status=200,
            timeout_seconds=args.timeout_seconds,
        ),
        check_json_status(
            name="metrics",
            url=f"{api_url}/api/harness/metrics?limit=20",
            expected_status=200,
            timeout_seconds=args.timeout_seconds,
        ),
    ]
    if args.frontend_url:
        results.append(
            check_page(
                name="frontend",
                url=args.frontend_url.rstrip("/"),
                timeout_seconds=args.timeout_seconds,
            )
        )

    payload = {
        "service": "socialease-agent",
        "api_url": api_url,
        "frontend_url": args.frontend_url or None,
        "checks": [result.__dict__ for result in results],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
