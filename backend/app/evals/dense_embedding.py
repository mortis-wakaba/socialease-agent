"""Optional local dense embedder used only by memory retrieval benchmarks."""

from collections.abc import Sequence
import hashlib
from pathlib import Path

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


class FastEmbedBgeSmallZh:
    """Pinned CPU/ONNX BGE adapter for reproducible Chinese retrieval evals."""

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
                    "Local embedding directory is incomplete; "
                    "missing or empty: "
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
                "Vector benchmark requires requirements-vector-eval.txt"
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
