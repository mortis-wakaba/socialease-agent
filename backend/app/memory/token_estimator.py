"""Pluggable token estimators for bounded model context construction."""

from __future__ import annotations

import math
from typing import Protocol


class TokenEstimator(Protocol):
    """Count model input tokens without coupling memory code to one provider."""

    backend_name: str
    model_name: str | None

    def count(self, text: str) -> int: ...


class ConservativeTokenEstimator:
    """Tokenizer-independent upper-leaning estimate for mixed Chinese/ASCII text."""

    backend_name = "conservative_heuristic"
    model_name = None

    def count(self, text: str) -> int:
        ascii_count = sum(1 for character in text if ord(character) < 128)
        non_ascii_count = len(text) - ascii_count
        return max(1, non_ascii_count + math.ceil(ascii_count / 4))


class TiktokenTokenEstimator:
    """Model-aware estimator for models supported by an installed tiktoken."""

    backend_name = "tiktoken"

    def __init__(self, *, model_name: str) -> None:
        import tiktoken

        self.model_name = model_name
        self._encoding = tiktoken.encoding_for_model(model_name)

    def count(self, text: str) -> int:
        return max(1, len(self._encoding.encode(text, disallowed_special=())))


def create_token_estimator(
    *,
    backend: str = "auto",
    model_name: str | None = None,
) -> TokenEstimator:
    """Select an exact supported tokenizer, otherwise fail closed to a heuristic."""
    normalized = backend.strip().casefold()
    if normalized not in {"auto", "heuristic", "tiktoken"}:
        normalized = "auto"
    if normalized == "heuristic":
        return ConservativeTokenEstimator()
    if model_name and (normalized == "tiktoken" or _looks_tiktoken_compatible(model_name)):
        try:
            return TiktokenTokenEstimator(model_name=model_name)
        except (ImportError, KeyError, ValueError):
            return ConservativeTokenEstimator()
    return ConservativeTokenEstimator()


def _looks_tiktoken_compatible(model_name: str) -> bool:
    normalized = model_name.casefold()
    return normalized.startswith(("gpt-", "o1", "o3", "o4", "text-embedding-"))
