"""Optional local FastEmbed Cross-Encoder adapter for memory evaluations."""

from collections.abc import Sequence
import hashlib
from pathlib import Path


BGE_RERANKER_MODEL = "BAAI/bge-reranker-base"
BGE_RERANKER_REVISION = "fastembed-catalog-v0.8.0"
_REQUIRED_LOCAL_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "onnx/model.onnx",
)


class FastEmbedBgeReranker:
    """Run the multilingual BGE reranker locally without a network API."""

    provider_name = "fastembed_local"
    model_name = BGE_RERANKER_MODEL
    model_revision = BGE_RERANKER_REVISION

    def __init__(
        self,
        *,
        cache_dir: str | None = None,
        threads: int | None = None,
        local_files_only: bool = False,
        specific_model_path: str | None = None,
    ) -> None:
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
                    "Local Cross-Encoder directory is incomplete; "
                    "missing or empty: "
                    + ", ".join(missing)
                )
            specific_model_path = str(model_dir)
            local_files_only = True
            self.model_revision = "sha256:" + _sha256(
                model_dir / "onnx/model.onnx"
            )
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as error:
            raise RuntimeError(
                "Cross-Encoder eval requires requirements-vector-eval.txt"
            ) from error
        self._model = TextCrossEncoder(
            model_name=self.model_name,
            cache_dir=cache_dir,
            threads=threads,
            local_files_only=local_files_only,
            specific_model_path=specific_model_path,
        )

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """Return one raw pair score for each summary."""
        if not documents:
            return []
        scores = [
            float(score)
            for score in self._model.rerank(query, list(documents), batch_size=20)
        ]
        if len(scores) != len(documents):
            raise RuntimeError("FastEmbed reranker returned an incomplete batch")
        return scores


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
