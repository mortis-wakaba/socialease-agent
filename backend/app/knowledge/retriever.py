"""BM25 retriever for local markdown knowledge chunks."""

import math
import re
from collections import Counter

from app.models_knowledge import KnowledgeChunk


class BM25Retriever:
    """Rank chunks with a small dependency-free BM25 implementation."""

    def __init__(
        self,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        min_score: float = 0.1,
        relative_score_threshold: float = 0.5,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.min_score = min_score
        self.relative_score_threshold = max(0.0, min(relative_score_threshold, 1.0))

    def retrieve(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
        limit: int = 3,
    ) -> list[tuple[KnowledgeChunk, float]]:
        """Return top chunks and BM25 scores."""
        if not chunks:
            return []
        query_terms = self._terms(query)
        if not query_terms:
            return []

        documents = [self._terms(chunk.text) for chunk in chunks]
        doc_lengths = [len(document) for document in documents]
        avg_doc_length = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 0.0
        document_frequencies = self._document_frequencies(documents)

        scored: list[tuple[KnowledgeChunk, float]] = []
        for chunk, document_terms, doc_length in zip(chunks, documents, doc_lengths, strict=True):
            term_counts = Counter(document_terms)
            score = 0.0
            for term in query_terms:
                tf = term_counts.get(term, 0)
                if tf == 0:
                    continue
                idf = self._idf(
                    document_frequency=document_frequencies.get(term, 0),
                    document_count=len(documents),
                )
                denominator = tf + self.k1 * (
                    1 - self.b + self.b * (doc_length / avg_doc_length)
                )
                score += idf * ((tf * (self.k1 + 1)) / denominator)
            if score >= self.min_score:
                scored.append((chunk, score))

        ranked = sorted(scored, key=lambda item: item[1], reverse=True)
        if not ranked:
            return []
        relative_floor = ranked[0][1] * self.relative_score_threshold
        return [item for item in ranked if item[1] >= relative_floor][:limit]

    @staticmethod
    def _document_frequencies(documents: list[list[str]]) -> dict[str, int]:
        frequencies: dict[str, int] = {}
        for document in documents:
            for term in set(document):
                frequencies[term] = frequencies.get(term, 0) + 1
        return frequencies

    @staticmethod
    def _idf(*, document_frequency: int, document_count: int) -> float:
        return math.log(1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))

    @staticmethod
    def _terms(text: str) -> list[str]:
        lowered = text.casefold()
        ascii_terms = re.findall(r"[a-z0-9_]+", lowered)
        cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
        cjk_bigrams: list[str] = []
        for term in cjk_terms:
            cjk_bigrams.extend(
                term[index : index + 2] for index in range(max(0, len(term) - 1))
            )
        terms = [*ascii_terms, *cjk_terms, *cjk_bigrams]
        stop_terms = {
            "我想", "可以", "怎么", "一个", "一下", "什么", "如果",
            "不知道", "不知", "知道", "先做", "比较", "合适", "练习",
        }
        return [term for term in terms if term not in stop_terms]


# Backwards-compatible alias for older imports/tests.
KeywordRetriever = BM25Retriever
