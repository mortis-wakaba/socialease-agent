"""Tests for configurable chunking and BM25 retrieval."""

from app.knowledge.chunker import ChunkConfig, MarkdownChunker
from app.knowledge.retriever import BM25Retriever
from app.models_knowledge import KnowledgeDocument, KnowledgeBaseType


def make_document(content: str, title: str = "Test Guide") -> KnowledgeDocument:
    """Create a minimal knowledge document for retrieval tests."""
    return KnowledgeDocument(
        title=title,
        source_name="Project Authored",
        source_type="project_authored",
        source_url=None,
        doc_type="guide",
        kb_type=KnowledgeBaseType.SOCIAL_SKILLS,
        audience="user_facing",
        review_status="reviewed",
        path=f"/tmp/{title}.md",
        content=content,
    )


def test_markdown_chunker_uses_configurable_size_and_overlap() -> None:
    document = make_document(
        "# Classroom\n\n"
        "课堂发言可以先准备核心观点。" * 12
        + "\n\n"
        + "练习时可以先写一句开场白。" * 12
    )
    chunker = MarkdownChunker(
        ChunkConfig(chunk_size=120, chunk_overlap=20, min_chunk_chars=20)
    )

    chunks = chunker.chunk([document])

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 120 for chunk in chunks)
    assert any("# Classroom" in chunk.text for chunk in chunks)


def test_bm25_retriever_ranks_relevant_chunk_first() -> None:
    chunks = MarkdownChunker(ChunkConfig(min_chunk_chars=5)).chunk(
        [
            make_document("课堂发言需要准备核心观点和开场白。", "Classroom Speech"),
            make_document("宿舍沟通需要表达边界和倾听。", "Dorm Conflict"),
        ]
    )
    retriever = BM25Retriever()

    results = retriever.retrieve("课堂发言 核心观点", chunks, limit=2)

    assert results
    assert results[0][0].title == "Classroom Speech"
    assert results[0][1] > 0
