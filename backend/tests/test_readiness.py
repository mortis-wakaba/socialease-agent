"""Deterministic async PostgreSQL engine URL tests."""

from app.db.postgres.engine import _async_psycopg_url


def test_async_psycopg_url_preserves_driver_qualifier() -> None:
    qualified = (
        "postgresql+psycopg://user:password@postgres:5432/socialease"
    )

    assert _async_psycopg_url(qualified) == qualified


def test_async_psycopg_url_adds_driver_qualifier() -> None:
    native = "postgresql://user:password@postgres:5432/socialease"

    assert _async_psycopg_url(native) == (
        "postgresql+psycopg://user:password@postgres:5432/socialease"
    )
