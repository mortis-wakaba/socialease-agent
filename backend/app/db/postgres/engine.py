"""Process-wide PostgreSQL engine reuse for repository adapters."""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@lru_cache(maxsize=8)
def shared_postgres_engine(database_url: str) -> Engine:
    """Return one thread-safe SQLAlchemy engine per exact database URL."""
    return create_engine(database_url, pool_pre_ping=True)
