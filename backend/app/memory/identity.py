"""Canonical identity and retention policy for durable episodic memory."""

from datetime import datetime, timedelta
from hashlib import sha256

from app.models_long_term_memory import MemoryType


MEMORY_CONSENT_VERSION = "practice-summary-v1"


def normalize_memory_summary(summary: str) -> str:
    """Normalize summary text exactly once for hashes and idempotency."""
    return " ".join(summary.casefold().split())


def memory_content_hash(summary: str) -> str:
    """Return the canonical content hash for one safe summary."""
    return sha256(normalize_memory_summary(summary).encode("utf-8")).hexdigest()


def memory_idempotency_key(
    *,
    user_id: str,
    source_type: str,
    memory_type: str,
    summary: str,
) -> str:
    """Return the canonical owner- and provenance-scoped write key."""
    material = "\0".join(
        (
            user_id,
            source_type,
            memory_type,
            normalize_memory_summary(summary),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


def memory_expiry(
    *,
    memory_type: MemoryType,
    created_at: datetime,
) -> datetime:
    """Apply the product retention policy for one durable memory type."""
    days = 730 if memory_type == MemoryType.HELPFUL_STRATEGY else 365
    return created_at + timedelta(days=days)
