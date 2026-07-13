"""Tests for configurable chunking and BM25 retrieval."""

from app.knowledge.chunker import ChunkConfig, MarkdownChunker
from app.knowledge.service import KnowledgeService
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


def test_bm25_retriever_filters_results_far_below_top_score() -> None:
    """A weak keyword overlap should not dilute one clearly dominant result."""
    chunks = MarkdownChunker(ChunkConfig(min_chunk_chars=5)).chunk(
        [
            make_document(
                "课堂发言练习需要准备核心观点，并从低强度开场白开始。",
                "Focused Classroom Guide",
            ),
            make_document("其他社交练习可以记录感受。", "Generic Practice Guide"),
        ]
    )
    retriever = BM25Retriever(relative_score_threshold=0.5)

    results = retriever.retrieve("课堂发言 低强度 核心观点 练习", chunks, limit=3)

    assert [chunk.title for chunk, _score in results] == ["Focused Classroom Guide"]


def test_bm25_retriever_keeps_multiple_results_with_comparable_scores() -> None:
    """Relative filtering should preserve several genuinely relevant chunks."""
    chunks = MarkdownChunker(ChunkConfig(min_chunk_chars=5)).chunk(
        [
            make_document("课堂发言练习可以先准备一句核心观点。", "Classroom Opening"),
            make_document("课堂发言练习可以准备一句简短开场白。", "Classroom Rehearsal"),
            make_document("宿舍卫生安排需要共同讨论。", "Dorm Chores"),
        ]
    )
    retriever = BM25Retriever(relative_score_threshold=0.5)

    results = retriever.retrieve("课堂发言练习", chunks, limit=3)

    assert {chunk.title for chunk, _score in results} == {
        "Classroom Opening",
        "Classroom Rehearsal",
    }


def test_successful_rag_answer_is_focused_and_source_labeled() -> None:
    """Successful retrieval should not expose markdown internals or unknown-case boilerplate."""
    response = KnowledgeService().query(
        "课堂发言前很紧张，有什么低强度的练习建议？",
        KnowledgeBaseType.SOCIAL_SKILLS,
    )

    assert not response.unknown
    assert "来源：Classroom Speech Practice Guide" in response.answer
    assert "markdown 知识库" not in response.answer
    assert "如果知识库没有具体资源" not in response.answer
    assert "# Classroom Speech Practice Guide" not in response.answer


def test_group_discussion_query_does_not_include_refusal_guidance() -> None:
    """Generic practice words must not pull an unrelated refusal scenario into the answer."""
    response = KnowledgeService().query(
        "小组讨论时我不知道怎么插话，先做什么练习比较合适？",
        KnowledgeBaseType.SOCIAL_SKILLS,
    )

    assert response.citations
    assert response.citations[0].title == "Group Discussion Practice Guide"
    assert all(citation.title != "Boundary and Refusal Practice Guide" for citation in response.citations)


def test_unavailable_campus_resource_query_returns_unknown() -> None:
    """School-specific requests must not fall through to generic public resources."""
    response = KnowledgeService().query(
        "学校心理中心电话和预约方式是什么？",
        KnowledgeBaseType.SUPPORT_RESOURCES,
    )

    assert response.unknown
    assert response.citations == []
    assert "不会编造" in response.answer
