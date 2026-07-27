"""Service layer for local markdown RAG retrieval."""

from app.knowledge.chunker import MarkdownChunker
from app.knowledge.formatter import CitationFormatter, citation_id_for_chunk
from app.knowledge.loader import MarkdownKnowledgeLoader
from app.knowledge.retriever import BM25Retriever
from app.models_knowledge import Citation, KnowledgeBaseType, KnowledgeQueryResponse


class KnowledgeService:
    """Coordinate loading, chunking, retrieving, and citation formatting."""

    def __init__(
        self,
        loader: MarkdownKnowledgeLoader | None = None,
        chunker: MarkdownChunker | None = None,
        retriever: BM25Retriever | None = None,
        formatter: CitationFormatter | None = None,
    ) -> None:
        self.loader = loader or MarkdownKnowledgeLoader()
        self.chunker = chunker or MarkdownChunker()
        self.retriever = retriever or BM25Retriever()
        self.formatter = formatter or CitationFormatter()

    def query(self, query: str, kb_type: KnowledgeBaseType) -> KnowledgeQueryResponse:
        """Query a selected knowledge base and return cited results."""
        if (
            kb_type == KnowledgeBaseType.SUPPORT_RESOURCES
            and _requests_unavailable_campus_resource(query)
        ):
            answer, citations, unknown, confidence, retrieval = self.formatter.format(
                [],
                retriever_name=self.retriever.__class__.__name__,
                top_k=3,
                query=query,
            )
            return KnowledgeQueryResponse(
                answer=answer,
                citations=citations,
                unknown=unknown,
                confidence=confidence,
                retrieval=retrieval,
            )
        documents = self.loader.load(kb_type)
        chunks = self.chunker.chunk(documents)
        results = self.retriever.retrieve(query=query, chunks=chunks)
        answer, citations, unknown, confidence, retrieval = self.formatter.format(
            results,
            retriever_name=self.retriever.__class__.__name__,
            top_k=3,
            query=query,
        )
        return KnowledgeQueryResponse(
            answer=answer,
            citations=citations,
            unknown=unknown,
            confidence=confidence,
            retrieval=retrieval,
        )

    def resolve_citations(
        self,
        citation_ids: list[str],
        *,
        kb_type: KnowledgeBaseType,
    ) -> list[Citation | None]:
        """Resolve stable IDs only against reviewed local knowledge chunks."""
        wanted = set(citation_ids)
        resolved: dict[str, Citation] = {}
        for chunk in self.chunker.chunk(self.loader.load(kb_type)):
            citation_id = citation_id_for_chunk(chunk)
            if citation_id not in wanted:
                continue
            resolved[citation_id] = Citation(
                citation_id=citation_id,
                title=chunk.title,
                source_name=chunk.source_name,
                source_type=chunk.source_type,
                source_url=chunk.source_url,
                snippet=self.formatter._snippet(chunk.text),
            )
        return [resolved.get(citation_id) for citation_id in citation_ids]


def _requests_unavailable_campus_resource(query: str) -> bool:
    """Return whether a query requires school-specific data not currently stored."""
    normalized = query.casefold()
    campus_terms = (
        "学校心理中心",
        "校心理中心",
        "校园心理中心",
        "学校咨询中心",
        "校内心理中心",
        "校内咨询中心",
        "学校资源",
        "校内资源",
        "辅导员联系方式",
        "campus counseling center",
        "campus support office",
    )
    return any(term in normalized for term in campus_terms)
