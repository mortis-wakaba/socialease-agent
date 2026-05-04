"""Chunk markdown documents for keyword retrieval."""

from app.models_knowledge import KnowledgeChunk, KnowledgeDocument


class MarkdownChunker:
    """Split documents into paragraph-sized chunks."""

    def __init__(self, max_chars: int = 700) -> None:
        self.max_chars = max_chars

    def chunk(self, documents: list[KnowledgeDocument]) -> list[KnowledgeChunk]:
        """Return chunks for a list of loaded documents."""
        chunks: list[KnowledgeChunk] = []
        for document in documents:
            paragraphs = [
                paragraph.strip()
                for paragraph in document.content.split("\n\n")
                if paragraph.strip()
            ]
            buffer = ""
            for paragraph in paragraphs:
                candidate = f"{buffer}\n\n{paragraph}".strip()
                if len(candidate) <= self.max_chars:
                    buffer = candidate
                    continue
                if buffer:
                    chunks.append(self._make_chunk(document, buffer))
                buffer = paragraph

            if buffer:
                chunks.append(self._make_chunk(document, buffer))
        return chunks

    @staticmethod
    def _make_chunk(document: KnowledgeDocument, text: str) -> KnowledgeChunk:
        return KnowledgeChunk(
            title=document.title,
            source=document.source,
            path=document.path,
            text=text,
        )

