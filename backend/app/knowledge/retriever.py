"""Keyword retriever for local knowledge chunks."""

import re

from app.models_knowledge import KnowledgeChunk


class KeywordRetriever:
    """Rank chunks by simple multilingual keyword overlap."""

    def __init__(self, min_score: int = 1) -> None:
        self.min_score = min_score

    def retrieve(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
        limit: int = 3,
    ) -> list[tuple[KnowledgeChunk, int]]:
        """Return top chunks and integer scores."""
        query_terms = self._terms(query)
        scored: list[tuple[KnowledgeChunk, int]] = []
        for chunk in chunks:
            text = chunk.text.casefold()
            score = 0
            for term in query_terms:
                if term in text:
                    score += 2 if len(term) > 1 else 1
            if score >= self.min_score:
                scored.append((chunk, score))

        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]

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
        stop_terms = {"我想", "可以", "怎么", "一个", "一下", "什么", "如果"}
        return sorted({term for term in terms if term not in stop_terms})

