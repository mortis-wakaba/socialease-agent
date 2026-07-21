#!/usr/bin/env python3
"""Reject commit candidates containing private documents or likely live secrets."""

from __future__ import annotations

from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.privacy.repository_check import collect_violations  # noqa: E402


def main() -> int:
    """Exit non-zero when a tracked privacy boundary is violated."""
    violations = collect_violations(REPOSITORY_ROOT)
    if violations:
        print("Repository privacy check failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Repository privacy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
