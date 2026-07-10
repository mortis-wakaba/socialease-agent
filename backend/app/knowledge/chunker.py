"""Configurable markdown chunking for local RAG retrieval."""

from dataclasses import dataclass

from app.models_knowledge import KnowledgeChunk, KnowledgeDocument


@dataclass(frozen=True)
class ChunkConfig:
    """Configuration for markdown chunk size and overlap."""

    chunk_size: int = 600
    chunk_overlap: int = 120
    min_chunk_chars: int = 80

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap cannot be negative")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.min_chunk_chars <= 0:
            raise ValueError("min_chunk_chars must be positive")


class MarkdownChunker:
    """Split markdown documents with configurable size and overlap."""

    def __init__(self, config: ChunkConfig | None = None, max_chars: int | None = None) -> None:
        if max_chars is not None and config is None:
            config = ChunkConfig(chunk_size=max_chars)
        self.config = config or ChunkConfig()

    def chunk(self, documents: list[KnowledgeDocument]) -> list[KnowledgeChunk]:
        """Return chunks for a list of loaded documents."""
        chunks: list[KnowledgeChunk] = []
        for document in documents:
            units = self._markdown_units(document.content)
            buffer = ""
            for unit in units:
                candidate = f"{buffer}\n\n{unit}".strip()
                if len(candidate) <= self.config.chunk_size:
                    buffer = candidate
                    continue
                if buffer:
                    chunks.extend(self._split_oversized(document, buffer))
                buffer = self._overlap_suffix(buffer, unit)

            if buffer:
                chunks.extend(self._split_oversized(document, buffer))
        return chunks

    def _split_oversized(self, document: KnowledgeDocument, text: str) -> list[KnowledgeChunk]:
        compact = text.strip()
        if len(compact) <= self.config.chunk_size:
            return [self._make_chunk(document, compact)] if len(compact) >= self.config.min_chunk_chars else []

        chunks: list[KnowledgeChunk] = []
        step = self.config.chunk_size - self.config.chunk_overlap
        start = 0
        while start < len(compact):
            piece = compact[start : start + self.config.chunk_size].strip()
            if len(piece) >= self.config.min_chunk_chars:
                chunks.append(self._make_chunk(document, piece))
            start += step
        return chunks

    def _overlap_suffix(self, previous: str, next_unit: str) -> str:
        if not previous:
            return next_unit
        suffix = previous[-self.config.chunk_overlap :].strip()
        return f"{suffix}\n\n{next_unit}".strip()

    @staticmethod
    def _markdown_units(content: str) -> list[str]:
        units: list[str] = []
        current_heading = ""
        for block in content.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#"):
                current_heading = block
                units.append(block)
                continue
            units.append(f"{current_heading}\n{block}".strip() if current_heading else block)
        return units

    @staticmethod
    def _make_chunk(document: KnowledgeDocument, text: str) -> KnowledgeChunk:
        return KnowledgeChunk(
            title=document.title,
            source_name=document.source_name,
            source_type=document.source_type,
            source_url=document.source_url,
            path=document.path,
            text=text,
        )
