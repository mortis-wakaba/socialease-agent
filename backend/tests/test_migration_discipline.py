"""Tests for Alembic migration discipline checks."""

from pathlib import Path

import pytest

from app.db.migration_check import (
    list_revision_files,
    migration_versions_dir,
    validate_revision_chain,
    validate_revision_filenames,
)


def test_existing_migration_files_follow_naming_convention() -> None:
    versions_dir = migration_versions_dir(Path.cwd())
    errors = validate_revision_filenames(list_revision_files(versions_dir))

    assert errors == []


def test_migration_revision_graph_is_valid() -> None:
    validate_revision_chain(Path.cwd())


def test_invalid_migration_filename_is_reported(tmp_path: Path) -> None:
    bad_revision = tmp_path / "add stuff.py"
    bad_revision.write_text("# demo", encoding="utf-8")

    errors = validate_revision_filenames([bad_revision])

    assert errors
    assert "expected format" in errors[0]


def test_duplicate_migration_prefix_is_reported(tmp_path: Path) -> None:
    first = tmp_path / "0002_add_users.py"
    second = tmp_path / "0002_add_protocols.py"

    errors = validate_revision_filenames([first, second])

    assert errors == ["0002_add_protocols.py: duplicate numeric migration prefix 0002"]
