"""Deterministic readiness adapter tests."""

from app.observability.readiness import _psycopg_dsn


def test_psycopg_dsn_removes_sqlalchemy_driver_qualifier() -> None:
    assert _psycopg_dsn(
        "postgresql+psycopg://user:password@postgres:5432/socialease"
    ) == "postgresql://user:password@postgres:5432/socialease"


def test_psycopg_dsn_preserves_native_postgres_url() -> None:
    native = "postgresql://user:password@postgres:5432/socialease"

    assert _psycopg_dsn(native) == native
