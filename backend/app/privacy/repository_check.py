"""Static privacy rules for files tracked by the project repository."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


FORBIDDEN_PREFIXES = (
    "resume_templates/",
    "docs/interview_prep/",
    "docs/local_plans/",
)
FORBIDDEN_NAMES = {
    "credentials.json",
    "token.json",
    "client_secret.json",
}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
}


def tracked_paths(root: Path) -> list[Path]:
    """Return tracked and untracked candidates without inspecting ignored files."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "-c", "-o", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [Path(value.decode()) for value in result.stdout.split(b"\0") if value]


def path_violation(path: Path) -> str | None:
    """Return the repository privacy rule violated by one tracked path."""
    normalized = path.as_posix()
    if any(normalized.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return "private_document_directory"
    if path.name in FORBIDDEN_NAMES:
        return "credential_file"
    if path.name == ".env" or (path.name.startswith(".env.") and not path.name.endswith(".example")):
        return "environment_file"
    return None


def content_violations(path: Path, root: Path) -> list[str]:
    """Return likely secret categories found in one small tracked text file."""
    absolute = root / path
    try:
        if absolute.stat().st_size > 1_000_000:
            return []
        text = absolute.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def collect_violations(root: Path) -> list[str]:
    """Collect path and content violations without returning secret values."""
    violations: list[str] = []
    for path in tracked_paths(root):
        path_error = path_violation(path)
        if path_error is not None:
            violations.append(f"{path_error}: {path.as_posix()}")
            continue
        for category in content_violations(path, root):
            violations.append(f"{category}: {path.as_posix()}")
    return violations
