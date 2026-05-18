"""Citation and answer formatter for retrieved knowledge chunks."""

from app.models_knowledge import Citation, KnowledgeChunk


class CitationFormatter:
    """Format retrieval results into short answers with citations."""

    def format(
        self,
        results: list[tuple[KnowledgeChunk, int]],
    ) -> tuple[str, list[Citation], bool, float]:
        """Return answer, citations, unknown flag, and confidence."""
        if not results:
            return (
                "我不知道。当前知识库没有找到足够相关的内容，因此不会编造资源、电话、热线或学校信息。",
                [],
                True,
                0.0,
            )

        citations = [
            Citation(
                title=chunk.title,
                source_name=chunk.source_name,
                source_type=chunk.source_type,
                source_url=chunk.source_url,
                snippet=self._snippet(chunk.text),
            )
            for chunk, _score in results
        ]
        answer_lines = [
            "根据当前 markdown 知识库，可以参考以下内容：",
            *[f"- {citation.snippet}" for citation in citations],
            "以上内容仅来自已收录文档；如果知识库没有具体资源，系统不会编造联系方式。",
        ]
        top_score = results[0][1]
        confidence = min(1.0, top_score / 10)
        return "\n".join(answer_lines), citations, False, confidence

    @staticmethod
    def _snippet(text: str, max_chars: int = 180) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        return f"{compact[: max_chars - 3]}..."
