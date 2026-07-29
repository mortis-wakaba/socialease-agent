"""Optional local FastEmbed Cross-Encoder adapter for memory evaluations."""

from collections.abc import Sequence


BGE_RERANKER_MODEL = "BAAI/bge-reranker-base"
BGE_RERANKER_REVISION = "fastembed-catalog-v0.8.0"


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
    ) -> None:
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
