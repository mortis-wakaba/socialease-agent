"""Validate that production Prompt changes are paired with explicit version bumps."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Sequence

from app.tracing.versions import PROMPT_VERSIONS


APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = APP_DIR.parent
REPOSITORY_ROOT = BACKEND_DIR.parent
MANIFEST_PATH = Path(__file__).with_name("prompt_versions.json")
MANIFEST_REPOSITORY_PATH = MANIFEST_PATH.relative_to(REPOSITORY_ROOT).as_posix()


@dataclass(frozen=True)
class PromptSource:
    """One source symbol whose AST contributes to a Prompt family fingerprint."""

    path: str
    symbols: tuple[str, ...]


PROMPT_SOURCES: dict[str, tuple[PromptSource, ...]] = {
    "safety_classifier": (
        PromptSource(
            "app/llm/prompts.py",
            ("build_safety_system_prompt", "build_safety_user_prompt"),
        ),
    ),
    "intent_router": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "COMMON_SAFETY_INSTRUCTIONS",
                "build_intent_router_system_prompt",
                "build_intent_router_user_prompt",
            ),
        ),
    ),
    "support_generation": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "COMMON_SAFETY_INSTRUCTIONS",
                "build_support_system_prompt",
                "build_support_user_prompt",
            ),
        ),
    ),
    "roleplay": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "COMMON_SAFETY_INSTRUCTIONS",
                "build_roleplay_system_prompt",
                "build_roleplay_user_prompt",
            ),
        ),
    ),
    "worksheet_extraction": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "COMMON_SAFETY_INSTRUCTIONS",
                "build_worksheet_system_prompt",
                "build_worksheet_user_prompt",
            ),
        ),
    ),
    "memory_extraction": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "COMMON_SAFETY_INSTRUCTIONS",
                "build_memory_extraction_system_prompt",
                "build_memory_extraction_user_prompt",
            ),
        ),
    ),
    "resource_agent_loop": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "COMMON_SAFETY_INSTRUCTIONS",
                "build_resource_loop_system_prompt",
                "build_resource_loop_user_prompt",
            ),
        ),
    ),
    "output_guardrail": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "build_output_guardrail_system_prompt",
                "build_output_guardrail_user_prompt",
            ),
        ),
    ),
    "output_repair": (
        PromptSource(
            "app/llm/prompts.py",
            (
                "build_output_repair_system_prompt",
                "build_output_repair_user_prompt",
            ),
        ),
    ),
    "roleplay_compaction": (
        PromptSource(
            "app/memory/roleplay_compactor.py",
            ("_compact_system_prompt", "_compact_user_prompt"),
        ),
    ),
    "conversation_compaction": (
        PromptSource(
            "app/conversation/compactor.py",
            ("_compact_system_prompt", "_compact_user_prompt"),
        ),
    ),
}


def build_prompt_manifest(*, backend_dir: Path = BACKEND_DIR) -> dict[str, Any]:
    """Build deterministic Prompt versions and AST fingerprints from current source."""
    registered = set(PROMPT_VERSIONS)
    sourced = set(PROMPT_SOURCES)
    if registered != sourced:
        missing_sources = sorted(registered - sourced)
        missing_versions = sorted(sourced - registered)
        raise ValueError(
            "Prompt registry mismatch: "
            f"missing_sources={missing_sources}, missing_versions={missing_versions}"
        )
    return {
        "schema_version": 1,
        "prompts": {
            name: {
                "version": PROMPT_VERSIONS[name],
                "fingerprint": _prompt_fingerprint(
                    PROMPT_SOURCES[name],
                    backend_dir=backend_dir,
                ),
            }
            for name in sorted(PROMPT_VERSIONS)
        },
    }


def validate_manifest(
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[str]:
    """Return actionable consistency errors for the committed Manifest."""
    errors: list[str] = []
    expected_prompts = _prompt_entries(expected)
    actual_prompts = _prompt_entries(actual)
    if set(expected_prompts) != set(actual_prompts):
        errors.append(
            "Prompt Manifest keys do not match the registry; run the update command."
        )
    for name in sorted(set(expected_prompts) & set(actual_prompts)):
        expected_entry = expected_prompts[name]
        actual_entry = actual_prompts[name]
        if actual_entry.get("version") != expected_entry.get("version"):
            errors.append(
                f"{name}: manifest version {actual_entry.get('version')!r} does not "
                f"match registry version {expected_entry.get('version')!r}."
            )
        if actual_entry.get("fingerprint") != expected_entry.get("fingerprint"):
            errors.append(
                f"{name}: Prompt source changed without refreshing its Manifest entry. "
                "Bump PROMPT_VERSIONS first, then run `make update-prompt-versions`."
            )
    return errors


def validate_version_bumps(
    *,
    current: dict[str, Any],
    previous: dict[str, Any],
) -> list[str]:
    """Require a version change whenever a baseline fingerprint changes."""
    errors: list[str] = []
    current_prompts = _prompt_entries(current)
    previous_prompts = _prompt_entries(previous)
    for name in sorted(set(current_prompts) & set(previous_prompts)):
        current_entry = current_prompts[name]
        previous_entry = previous_prompts[name]
        if (
            current_entry.get("fingerprint") != previous_entry.get("fingerprint")
            and current_entry.get("version") == previous_entry.get("version")
        ):
            errors.append(
                f"{name}: Prompt fingerprint changed but version stayed at "
                f"{current_entry.get('version')!r}."
            )
    return errors


def update_manifest(*, manifest_path: Path = MANIFEST_PATH) -> None:
    """Refresh the Manifest only after every changed Prompt has a new version."""
    current = build_prompt_manifest()
    if manifest_path.exists():
        previous = _read_manifest(manifest_path)
        errors = validate_version_bumps(current=current, previous=previous)
        if errors:
            raise ValueError("\n".join(errors))
    manifest_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def check_prompt_versions(
    *,
    manifest_path: Path = MANIFEST_PATH,
    base_ref: str | None = None,
) -> list[str]:
    """Check current consistency and, when supplied, compare with a Git baseline."""
    if not manifest_path.exists():
        return [f"Prompt Manifest is missing: {manifest_path}"]
    committed = _read_manifest(manifest_path)
    errors = validate_manifest(expected=build_prompt_manifest(), actual=committed)
    if base_ref and base_ref.strip("0"):
        previous = _read_manifest_from_git(base_ref)
        if previous is not None:
            errors.extend(validate_version_bumps(current=committed, previous=previous))
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Prompt version check or intentionally refresh its Manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="Git SHA/ref used to enforce version bumps")
    parser.add_argument(
        "--update",
        action="store_true",
        help="refresh fingerprints after bumping changed PROMPT_VERSIONS entries",
    )
    args = parser.parse_args(argv)
    try:
        if args.update:
            update_manifest()
            print(f"Updated Prompt version Manifest: {MANIFEST_REPOSITORY_PATH}")
            return 0
        errors = check_prompt_versions(base_ref=args.base_ref)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Prompt version check failed: {exc}")
        return 1
    if errors:
        print("Prompt version check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Prompt version check passed.")
    return 0


def _prompt_fingerprint(
    sources: tuple[PromptSource, ...],
    *,
    backend_dir: Path,
) -> str:
    digest = hashlib.sha256()
    for source in sources:
        path = backend_dir / source.path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        symbols = _top_level_symbols(tree)
        for symbol in source.symbols:
            node = symbols.get(symbol)
            if node is None:
                raise ValueError(f"Prompt source symbol not found: {source.path}:{symbol}")
            digest.update(source.path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(symbol.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_normalized_ast_dump(node).encode("utf-8"))
            digest.update(b"\0")
    return f"sha256:{digest.hexdigest()[:16]}"


def _top_level_symbols(tree: ast.Module) -> dict[str, ast.AST]:
    symbols: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols[target.id] = node
    return symbols


def _normalized_ast_dump(node: ast.AST) -> str:
    """Exclude Python docstrings while retaining Prompt-producing code and defaults."""
    normalized = deepcopy(node)
    if isinstance(normalized, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if (
            normalized.body
            and isinstance(normalized.body[0], ast.Expr)
            and isinstance(normalized.body[0].value, ast.Constant)
            and isinstance(normalized.body[0].value.value, str)
        ):
            normalized.body = normalized.body[1:]
    return ast.dump(normalized, include_attributes=False)


def _prompt_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    prompts = manifest.get("prompts")
    if not isinstance(prompts, dict):
        return {}
    return {
        str(name): entry
        for name, entry in prompts.items()
        if isinstance(entry, dict)
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Prompt Manifest must be a JSON object: {path}")
    return value


def _read_manifest_from_git(base_ref: str) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{MANIFEST_REPOSITORY_PATH}"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # The first commit that introduces governance has no baseline Manifest.
        return None
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("Baseline Prompt Manifest must be a JSON object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
