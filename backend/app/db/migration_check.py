"""Migration discipline checks for Alembic revisions."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from alembic import command
from alembic.config import Config


MIGRATION_FILENAME_PATTERN = re.compile(r"^\d{4}_[a-z0-9_]+\.py$")


def migration_versions_dir(root: Path | None = None) -> Path:
    """Return the Alembic versions directory."""
    backend_root = _resolve_backend_root(root or Path(__file__).resolve().parents[2])
    return backend_root / "migrations" / "versions"


def list_revision_files(versions_dir: Path) -> list[Path]:
    """Return migration revision files, excluding cache files."""
    return sorted(path for path in versions_dir.glob("*.py") if path.is_file())


def validate_revision_filenames(revision_files: list[Path]) -> list[str]:
    """Return filename violations for migration revisions."""
    errors: list[str] = []
    seen_prefixes: set[str] = set()
    for path in revision_files:
        if not MIGRATION_FILENAME_PATTERN.match(path.name):
            errors.append(
                f"{path.name}: expected format 0001_short_snake_case_description.py"
            )
            continue
        prefix = path.name.split("_", 1)[0]
        if prefix in seen_prefixes:
            errors.append(f"{path.name}: duplicate numeric migration prefix {prefix}")
        seen_prefixes.add(prefix)
    return errors


def validate_revision_chain(backend_root: Path) -> None:
    """Validate migration naming and revision graph without connecting to a database."""
    backend_root = _resolve_backend_root(backend_root)
    versions_dir = migration_versions_dir(backend_root)
    revision_files = list_revision_files(versions_dir)
    errors = validate_revision_filenames(revision_files)
    if errors:
        joined = "\n".join(f"- {error}" for error in errors)
        raise RuntimeError(f"Migration filename validation failed:\n{joined}")
    config = _alembic_config(backend_root)
    command.heads(config)


def run_database_upgrade(backend_root: Path, database_url: str) -> None:
    """Run Alembic migrations to head against a live database."""
    backend_root = _resolve_backend_root(backend_root)
    config = _alembic_config(backend_root)
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    command.current(config, check_heads=True)


def _alembic_config(backend_root: Path) -> Config:
    """Return an Alembic config whose paths do not depend on process cwd."""
    backend_root = _resolve_backend_root(backend_root)
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("prepend_sys_path", str(backend_root))
    return config


def _resolve_backend_root(root: Path) -> Path:
    """Accept either the repository root or backend root."""
    candidate = root.resolve()
    if (candidate / "alembic.ini").is_file() and (candidate / "migrations").is_dir():
        return candidate
    backend_candidate = candidate / "backend"
    if (
        (backend_candidate / "alembic.ini").is_file()
        and (backend_candidate / "migrations").is_dir()
    ):
        return backend_candidate
    return candidate


def main() -> None:
    """Run migration discipline checks from the command line."""
    parser = argparse.ArgumentParser(description="Validate SocialEase Alembic migrations.")
    parser.add_argument(
        "--check-names-only",
        action="store_true",
        help="Validate revision filenames and graph without connecting to a database.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("SOCIALEASE_DATABASE_URL"),
        help="Database URL used for alembic upgrade head.",
    )
    args = parser.parse_args()

    backend_root = Path(__file__).resolve().parents[2]
    validate_revision_chain(backend_root)
    if not args.check_names_only:
        if not args.database_url:
            raise RuntimeError(
                "SOCIALEASE_DATABASE_URL or --database-url is required for live migration checks."
            )
        run_database_upgrade(backend_root, args.database_url)
    print("Migration discipline check passed.")


if __name__ == "__main__":
    main()
