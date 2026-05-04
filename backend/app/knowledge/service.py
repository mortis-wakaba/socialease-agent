"""Service layer for the dual knowledge-base RAG MVP."""

from app.knowledge.chunker import MarkdownChunker
from app.knowledge.formatter import CitationFormatter
from app.knowledge.loader import MarkdownKnowledgeLoader
from app.knowledge.retriever import KeywordRetriever
from app.models_knowledge import KnowledgeBaseType, KnowledgeQueryResponse


class KnowledgeService:
    """Coordinate loading, chunking, retrieving, and citation formatting."""

    def __init__(
        self,
        loader: MarkdownKnowledgeLoader | None = None,
        chunker: MarkdownChunker | None = None,
        retriever: KeywordRetriever | None = None,
        formatter: CitationFormatter | None = None,
    ) -> None:
        self.loader = loader or MarkdownKnowledgeLoader()
        self.chunker = chunker or MarkdownChunker()
        self.retriever = retriever or KeywordRetriever()
        self.formatter = formatter or CitationFormatter()

    def query(self, query: str, kb_type: KnowledgeBaseType) -> KnowledgeQueryResponse:
        """Query a selected knowledge base and return cited results."""
        documents = self.loader.load(kb_type)
        chunks = self.chunker.chunk(documents)
        results = self.retriever.retrieve(query=query, chunks=chunks)
        answer, citations, unknown, confidence = self.formatter.format(results)
        return KnowledgeQueryResponse(
            answer=answer,
            citations=citations,
            unknown=unknown,
            confidence=confidence,
        )

