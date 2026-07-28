"""PostgreSQL integration coverage for the cleanup scheduler advisory lock."""

import os

import pytest

from app.jobs.cleanup_lock import PostgresAdvisoryCleanupRunLock


TEST_DATABASE_URL = os.getenv("SOCIALEASE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="SOCIALEASE_TEST_DATABASE_URL is required for PostgreSQL integration tests.",
)


@pytest.mark.anyio
async def test_postgres_cleanup_lock_allows_only_one_scheduler_replica() -> None:
    assert TEST_DATABASE_URL is not None
    first = PostgresAdvisoryCleanupRunLock(TEST_DATABASE_URL)
    second = PostgresAdvisoryCleanupRunLock(TEST_DATABASE_URL)

    try:
        assert await first.acquire() is True
        assert await second.acquire() is False
        await first.release()
        assert await second.acquire() is True
    finally:
        await first.release()
        await second.release()
