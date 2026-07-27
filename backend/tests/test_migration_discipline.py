"""Tests for Alembic migration discipline checks."""

from pathlib import Path
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.db.migration_check import (
    list_revision_files,
    migration_versions_dir,
    validate_revision_chain,
    validate_revision_filenames,
    validate_revision_identifiers,
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


def test_revision_identifier_must_fit_default_alembic_version_table() -> None:
    errors = validate_revision_identifiers(
        [
            "0010_open_scenario_checkpoint",
            "0010_add_open_scenario_checkpoint_metadata",
        ]
    )

    assert errors == [
        (
            "0010_add_open_scenario_checkpoint_metadata: revision identifier "
            "exceeds 32 characters"
        )
    ]


def test_long_term_memory_migration_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase 1 migration must upgrade, downgrade, and re-upgrade cleanly."""
    database_path = tmp_path / "migration-round-trip.db"
    monkeypatch.setenv(
        "SOCIALEASE_DATABASE_URL",
        f"sqlite:///{database_path}",
    )
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    assert _table_names(database_path) >= {
        "episodic_memories",
        "thread_checkpoints",
        "memory_events",
        "memory_proposals",
    }

    command.downgrade(config, "0006_add_session_reviews")
    assert not (
        {
            "episodic_memories",
            "thread_checkpoints",
            "memory_events",
            "memory_proposals",
        }
        & _table_names(database_path)
    )

    command.upgrade(config, "head")
    assert _table_names(database_path) >= {
        "episodic_memories",
        "thread_checkpoints",
        "memory_events",
        "memory_proposals",
    }


def _table_names(database_path: Path) -> set[str]:
    """Return SQLite table names for migration assertions."""
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {row[0] for row in rows}
