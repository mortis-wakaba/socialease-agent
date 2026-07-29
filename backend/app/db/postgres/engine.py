"""Process-wide async PostgreSQL engine lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache
import os

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)


_BOUND_CONNECTION: ContextVar[AsyncConnection | None] = ContextVar(
    "postgres_bound_connection",
    default=None,
)


@lru_cache(maxsize=8)
def shared_postgres_async_engine(database_url: str) -> AsyncEngine:
    """Return one engine per URL; isolated tests avoid cross-loop pooling."""
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if os.getenv("SOCIALEASE_TEST_DATABASE_URL") == database_url:
        engine_options["poolclass"] = NullPool
    engine = create_async_engine(
        _async_psycopg_url(database_url),
        **engine_options,
    )
    _ENGINE_REGISTRY.add(engine)
    return engine


async def dispose_shared_postgres_engines() -> None:
    """Drain all cached connection pools during application shutdown."""
    for engine in tuple(_ENGINE_REGISTRY):
        await engine.dispose()
    _ENGINE_REGISTRY.clear()
    shared_postgres_async_engine.cache_clear()


@asynccontextmanager
async def postgres_transaction(
    engine: AsyncEngine,
) -> AsyncIterator[AsyncConnection]:
    """Bind one connection so cooperating repositories share one transaction."""
    bound = _BOUND_CONNECTION.get()
    if bound is not None:
        yield bound
        return
    async with engine.begin() as connection:
        token = _BOUND_CONNECTION.set(connection)
        try:
            yield connection
        finally:
            _BOUND_CONNECTION.reset(token)


@asynccontextmanager
async def postgres_write_connection(
    engine: AsyncEngine,
) -> AsyncIterator[AsyncConnection]:
    """Reuse a bound transaction or open a short independent write transaction."""
    bound = _BOUND_CONNECTION.get()
    if bound is not None:
        yield bound
        return
    async with engine.begin() as connection:
        yield connection


@asynccontextmanager
async def postgres_read_connection(
    engine: AsyncEngine,
) -> AsyncIterator[AsyncConnection]:
    """Reuse a bound transaction or open a short read connection."""
    bound = _BOUND_CONNECTION.get()
    if bound is not None:
        yield bound
        return
    async with engine.connect() as connection:
        yield connection


def _async_psycopg_url(database_url: str) -> str:
    """Select psycopg 3's native async SQLAlchemy dialect explicitly."""
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    if database_url.startswith("postgres://"):
        return database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )
    return database_url


_ENGINE_REGISTRY: set[AsyncEngine] = set()
