"""Backfill versioned pgvector embeddings for existing episodic memories."""

from __future__ import annotations

import argparse
import asyncio

from app.db.factory import repository_factory
from app.db.postgres.long_term_memory_repository import (
    PostgresLongTermMemoryRepository,
)


async def _run(limit: int) -> int:
    repository = repository_factory().long_term_memory_repository()
    if not isinstance(repository, PostgresLongTermMemoryRepository):
        raise RuntimeError("Embedding backfill requires PostgreSQL")
    updated = await repository.backfill_embeddings(limit=limit)
    print(f"Backfilled {updated} episodic memory embeddings.")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()
    asyncio.run(_run(args.limit))


if __name__ == "__main__":
    main()
