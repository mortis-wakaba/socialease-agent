"""Tests for deterministic Prompt source/version governance."""

from copy import deepcopy
from pathlib import Path

from app.llm.prompt_version_check import (
    PROMPT_SOURCES,
    build_prompt_manifest,
    validate_manifest,
    validate_version_bumps,
)
from app.tracing.versions import PROMPT_VERSIONS


def test_every_versioned_prompt_has_a_source_fingerprint() -> None:
    manifest = build_prompt_manifest()

    assert set(PROMPT_SOURCES) == set(PROMPT_VERSIONS)
    assert set(manifest["prompts"]) == set(PROMPT_VERSIONS)
    assert all(
        entry["fingerprint"].startswith("sha256:")
        for entry in manifest["prompts"].values()
    )


def test_manifest_detects_changed_prompt_source(tmp_path: Path) -> None:
    source_backend = Path(__file__).resolve().parents[1]
    prompt_path = tmp_path / "app" / "llm"
    prompt_path.mkdir(parents=True)
    original = source_backend / "app" / "llm" / "prompts.py"
    (prompt_path / "prompts.py").write_text(
        original.read_text(encoding="utf-8").replace(
            "Return JSON only. Be conservative",
            "Return JSON only. Always be conservative",
        ),
        encoding="utf-8",
    )
    compactor_path = tmp_path / "app" / "memory"
    compactor_path.mkdir(parents=True)
    original_compactor = source_backend / "app" / "memory" / "roleplay_compactor.py"
    (compactor_path / "roleplay_compactor.py").write_text(
        original_compactor.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    committed = build_prompt_manifest()
    changed = build_prompt_manifest(backend_dir=tmp_path)

    errors = validate_manifest(expected=changed, actual=committed)

    assert any("safety_classifier" in error for error in errors)


def test_prompt_fingerprint_ignores_python_docstring_only_changes(
    tmp_path: Path,
) -> None:
    source_backend = Path(__file__).resolve().parents[1]
    prompt_path = tmp_path / "app" / "llm"
    prompt_path.mkdir(parents=True)
    original = source_backend / "app" / "llm" / "prompts.py"
    (prompt_path / "prompts.py").write_text(
        original.read_text(encoding="utf-8").replace(
            '"""Return strict instructions for semantic safety classification."""',
            '"""Updated Python documentation only."""',
        ),
        encoding="utf-8",
    )
    compactor_path = tmp_path / "app" / "memory"
    compactor_path.mkdir(parents=True)
    original_compactor = source_backend / "app" / "memory" / "roleplay_compactor.py"
    (compactor_path / "roleplay_compactor.py").write_text(
        original_compactor.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert build_prompt_manifest(backend_dir=tmp_path) == build_prompt_manifest()


def test_changed_fingerprint_requires_changed_version() -> None:
    previous = build_prompt_manifest()
    current = deepcopy(previous)
    current["prompts"]["support_generation"]["fingerprint"] = "sha256:changed"

    errors = validate_version_bumps(current=current, previous=previous)

    assert errors == [
        "support_generation: Prompt fingerprint changed but version stayed at "
        "'support-v3'."
    ]


def test_changed_fingerprint_passes_after_version_bump() -> None:
    previous = build_prompt_manifest()
    current = deepcopy(previous)
    current["prompts"]["support_generation"] = {
        "fingerprint": "sha256:changed",
        "version": "support-v4",
    }

    assert validate_version_bumps(current=current, previous=previous) == []
