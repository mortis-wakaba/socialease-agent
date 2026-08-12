"""Pinned local embeddings shared by runtime memory retrieval and evaluations."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import asyncio
import hashlib
import os
from pathlib import Path
from threading import Lock
from typing import Callable, TypeVar
from weakref import WeakKeyDictionary

from app.memory.recall import DenseEmbeddingProvider


BGE_SMALL_ZH_MODEL = "BAAI/bge-small-zh-v1.5"
BGE_SMALL_ZH_FASTEMBED_REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
BGE_SMALL_ZH_DIMENSIONS = 512
BGE_SMALL_ZH_MODEL_SIZE_MB = 90.0
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
_REQUIRED_LOCAL_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "model_optimized.onnx",
)
_EmbeddingResult = TypeVar("_EmbeddingResult")
_EMBEDDING_LIMITERS: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    asyncio.Semaphore,
] = WeakKeyDictionary()


class FastEmbedBgeSmallZh:
    """Pinned CPU/ONNX BGE adapter for Chinese memory retrieval."""

    provider_name = "fastembed"
    model_name = BGE_SMALL_ZH_MODEL
    model_revision = BGE_SMALL_ZH_FASTEMBED_REVISION
    dimensions = BGE_SMALL_ZH_DIMENSIONS
    model_size_mb = BGE_SMALL_ZH_MODEL_SIZE_MB

    def __init__(
        self,
        *,
        cache_dir: str | None = None,
        local_files_only: bool = False,
        threads: int | None = None,
        specific_model_path: str | None = None,
    ) -> None:
        using_specific_model = specific_model_path is not None
        if specific_model_path is not None:
            model_dir = Path(specific_model_path).expanduser().resolve()
            missing = [
                relative_path
                for relative_path in _REQUIRED_LOCAL_FILES
                if not (model_dir / relative_path).is_file()
                or (model_dir / relative_path).stat().st_size == 0
            ]
            if missing:
                raise RuntimeError(
                    "Local embedding directory is incomplete; missing or empty: "
                    + ", ".join(missing)
                )
            specific_model_path = str(model_dir)
            local_files_only = True
            self.model_revision = "sha256:" + _sha256(
                model_dir / "model_optimized.onnx"
            )
        try:
            from fastembed import TextEmbedding
        except ImportError as error:
            raise RuntimeError(
                "Vector memory retrieval requires fastembed."
            ) from error
        self._model = TextEmbedding(
            model_name=self.model_name,
            cache_dir=cache_dir,
            threads=threads,
            local_files_only=local_files_only,
            specific_model_path=specific_model_path,
        )
        model_impl = getattr(self._model, "model", None)
        model_dir = getattr(model_impl, "_model_dir", None)
        if (
            not using_specific_model
            and model_dir is not None
            and self.model_revision not in str(model_dir)
        ):
            raise RuntimeError(
                "FastEmbed resolved an unexpected model revision: "
                f"{model_dir}"
            )
        if self._model.embedding_size != self.dimensions:
            raise RuntimeError(
                "Unexpected BGE embedding dimensions: "
                f"{self._model.embedding_size} != {self.dimensions}"
            )

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed retrieval queries with the model-card instruction prefix."""
        return self._embed([BGE_QUERY_INSTRUCTION + text for text in texts])

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed memory summaries without a query instruction."""
        return self._embed(list(texts))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [
            vector.astype(float).tolist()
            for vector in self._model.embed(texts, batch_size=64)
        ]
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding provider returned an incomplete batch")
        return [_normalize(vector) for vector in vectors]


class LazyFastEmbedBgeSmallZh:
    """Load the pinned ONNX model on first background embedding call."""

    provider_name = FastEmbedBgeSmallZh.provider_name
    model_name = FastEmbedBgeSmallZh.model_name
    model_revision = FastEmbedBgeSmallZh.model_revision
    dimensions = FastEmbedBgeSmallZh.dimensions
    model_size_mb = FastEmbedBgeSmallZh.model_size_mb

    def __init__(
        self,
        *,
        cache_dir: str | None,
        local_files_only: bool,
        threads: int,
        specific_model_path: str | None,
    ) -> None:
        self._settings = {
            "cache_dir": cache_dir,
            "local_files_only": local_files_only,
            "threads": threads,
            "specific_model_path": specific_model_path,
        }
        self._provider: FastEmbedBgeSmallZh | None = None
        self._lock = Lock()
        if specific_model_path is not None:
            model_path = Path(specific_model_path).expanduser().resolve()
            model_file = model_path / "model_optimized.onnx"
            if model_file.is_file():
                self.model_revision = "sha256:" + _sha256(model_file)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._resolve().embed_queries(texts)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._resolve().embed_documents(texts)

    def _resolve(self) -> FastEmbedBgeSmallZh:
        if self._provider is not None:
            return self._provider
        with self._lock:
            if self._provider is None:
                self._provider = FastEmbedBgeSmallZh(**self._settings)
                self.model_revision = self._provider.model_revision
        return self._provider


def memory_retrieval_mode() -> str:
    """Return the validated runtime candidate-recall mode."""
    value = os.getenv("SOCIALEASE_MEMORY_RETRIEVAL_MODE", "sql_text").strip().casefold()
    if value not in {"sql_text", "postgres_fts", "dense_only", "hybrid"}:
        raise ValueError(
            "SOCIALEASE_MEMORY_RETRIEVAL_MODE must be sql_text, postgres_fts, "
            "dense_only, or hybrid"
        )
    return value


def create_memory_embedding_provider() -> DenseEmbeddingProvider | None:
    """Create one process-shared embedder for retrieval modes that need vectors."""
    if memory_retrieval_mode() not in {"dense_only", "hybrid"}:
        return None
    return _cached_embedding_provider(
        os.getenv("SOCIALEASE_MEMORY_EMBEDDING_MODEL_PATH", "").strip(),
        os.getenv("SOCIALEASE_MEMORY_EMBEDDING_CACHE_DIR", "").strip(),
        os.getenv("SOCIALEASE_MEMORY_EMBEDDING_LOCAL_ONLY", "false").casefold()
        == "true",
        int(os.getenv("SOCIALEASE_MEMORY_EMBEDDING_THREADS", "2")),
    )


@lru_cache(maxsize=4)
def _cached_embedding_provider(
    specific_model_path: str,
    cache_dir: str,
    local_files_only: bool,
    threads: int,
) -> LazyFastEmbedBgeSmallZh:
    return LazyFastEmbedBgeSmallZh(
        specific_model_path=specific_model_path or None,
        cache_dir=cache_dir or None,
        local_files_only=local_files_only,
        threads=max(1, threads),
    )


def vector_literal(vector: Sequence[float], *, dimensions: int) -> str:
    """Serialize one validated vector for a parameterized pgvector cast."""
    if len(vector) != dimensions:
        raise ValueError("Memory embedding has an unexpected dimension")
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


async def run_embedding(
    operation: Callable[[Sequence[str]], _EmbeddingResult],
    texts: Sequence[str],
) -> _EmbeddingResult:
    """Run CPU inference on a process-bounded executor outside the event loop."""
    loop = asyncio.get_running_loop()
    limiter = _EMBEDDING_LIMITERS.get(loop)
    if limiter is None:
        limiter = asyncio.Semaphore(_embedding_concurrency())
        _EMBEDDING_LIMITERS[loop] = limiter
    async with limiter:
        # A per-call executor avoids retaining an event-loop-owned default executor.
        # The limiter still bounds process CPU load and thread count.
        executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="memory-embedding",
        )
        try:
            future = loop.run_in_executor(executor, operation, texts)
            return await asyncio.wait_for(
                future,
                timeout=_embedding_timeout_seconds(),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def _embedding_concurrency() -> int:
    value = os.getenv("SOCIALEASE_MEMORY_EMBEDDING_CONCURRENCY", "2")
    try:
        configured = int(value)
    except ValueError as error:
        raise ValueError(
            "SOCIALEASE_MEMORY_EMBEDDING_CONCURRENCY must be an integer"
        ) from error
    return min(max(configured, 1), 8)


def _embedding_timeout_seconds() -> float:
    value = os.getenv("SOCIALEASE_MEMORY_EMBEDDING_TIMEOUT_SECONDS", "30")
    try:
        configured = float(value)
    except ValueError as error:
        raise ValueError(
            "SOCIALEASE_MEMORY_EMBEDDING_TIMEOUT_SECONDS must be numeric"
        ) from error
    return min(max(configured, 0.1), 120.0)


def _normalize(vector: list[float]) -> list[float]:
    norm = sum(value * value for value in vector) ** 0.5
    if norm <= 0:
        raise RuntimeError("Embedding provider returned a zero vector")
    return [value / norm for value in vector]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
