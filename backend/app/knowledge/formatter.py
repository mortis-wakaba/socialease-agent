"""Citation, answer, and retrieval diagnostics formatter."""

import re

from app.models_knowledge import Citation, KnowledgeChunk, RetrievalDiagnostics, RetrievalHit


class CitationFormatter:
    """Format retrieval results into short answers with citations."""

    def format(
        self,
        results: list[tuple[KnowledgeChunk, float]],
        *,
        retriever_name: str = "bm25",
        top_k: int = 3,
        query: str = "",
    ) -> tuple[str, list[Citation], bool, float, RetrievalDiagnostics]:
        """Return answer, citations, unknown flag, confidence, and diagnostics."""
        diagnostics = RetrievalDiagnostics(
            retriever=retriever_name,
            top_k=top_k,
            hits=[
                RetrievalHit(
                    title=chunk.title,
                    score=round(score, 4),
                    source_type=chunk.source_type,
                )
                for chunk, score in results
            ],
        )
        if not results:
            return (
                "我不知道。当前知识库没有找到足够相关的内容，因此不会编造资源、电话、热线或学校信息。",
                [],
                True,
                0.0,
                diagnostics,
            )

        citations = [
            Citation(
                title=chunk.title,
                source_name=chunk.source_name,
                source_type=chunk.source_type,
                source_url=chunk.source_url,
                snippet=self._snippet(chunk.text, query=query),
            )
            for chunk, _score in results
        ]
        answer_lines = [
            "根据当前 markdown 知识库，可以参考以下内容：",
            *[f"- {citation.snippet}" for citation in citations],
            "以上内容仅来自已收录文档；如果知识库没有具体资源，系统不会编造联系方式。",
        ]
        top_score = results[0][1]
        confidence = min(1.0, top_score / (top_score + 3.0))
        return "\n".join(answer_lines), citations, False, confidence, diagnostics

    @classmethod
    def _snippet(cls, text: str, *, query: str = "", max_chars: int = 180) -> str:
        compact = " ".join(text.split())
        focused = cls._focused_excerpt(compact, query) if query else compact
        if len(focused) <= max_chars:
            return focused
        return f"{focused[: max_chars - 3]}..."

    @staticmethod
    def _focused_excerpt(text: str, query: str) -> str:
        terms = [
            term
            for term in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", query.casefold())
            if term not in {"我想", "可以", "怎么", "一个", "一下", "什么", "如果"}
        ]
        if not terms:
            return text
        sentences = re.split(r"(?<=[。！？.!?])\s*|\n+", text)
        for sentence in sentences:
            if any(term in sentence.casefold() for term in terms):
                return sentence.strip() or text
        return text
