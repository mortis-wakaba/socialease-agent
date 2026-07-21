"""Tests for tracked-file privacy policy checks."""

from pathlib import Path

from app.privacy.repository_check import content_violations, path_violation


def test_private_and_credential_paths_are_rejected() -> None:
    assert path_violation(Path("docs/interview_prep/notes.md")) == "private_document_directory"
    assert path_violation(Path("resume_templates/resume.tex")) == "private_document_directory"
    assert path_violation(Path("credentials.json")) == "credential_file"
    assert path_violation(Path("backend/.env")) == "environment_file"


def test_example_environment_files_are_allowed() -> None:
    assert path_violation(Path(".env.example")) is None
    assert path_violation(Path(".env.production.example")) is None
    assert path_violation(Path("backend/.env.example")) is None


def test_secret_scan_reports_category_without_returning_secret(tmp_path: Path) -> None:
    secret = "sk-" + "a" * 24
    fixture = tmp_path / "fixture.txt"
    fixture.write_text(f"API_KEY={secret}", encoding="utf-8")

    violations = content_violations(Path("fixture.txt"), tmp_path)

    assert violations == ["openai_style_key"]
    assert secret not in str(violations)
