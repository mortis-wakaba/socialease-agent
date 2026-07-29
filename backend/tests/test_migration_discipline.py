"""Tests for Alembic migration discipline checks."""

from pathlib import Path
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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


def test_long_term_memory_tables_exist_at_postgres_head() -> None:
    """A configured PostgreSQL test database upgrades to the memory schema."""
    database_url = os.getenv("SOCIALEASE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SOCIALEASE_TEST_DATABASE_URL is required.")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        table_names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert table_names >= {
        "episodic_memories",
        "thread_checkpoints",
        "memory_events",
        "memory_proposals",
    }
