"""Shared domain types used by stable and episodic memory models."""

from enum import Enum


class MemoryType(str, Enum):
    """User-controllable categories eligible for episodic personalization."""

    PRACTICE_EXPERIENCE = "practice_experience"
    HELPFUL_STRATEGY = "helpful_strategy"
    PRACTICE_MILESTONE = "practice_milestone"
    SOCIAL_CONTEXT = "social_context"
    RECURRING_PATTERN = "recurring_pattern"
