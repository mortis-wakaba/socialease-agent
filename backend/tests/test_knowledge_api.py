"""API tests for the dual knowledge-base RAG MVP."""

import re

import httpx
import pytest

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for knowledge API tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_social_skills_knowledge_can_retrieve(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "课堂发言 怎么准备 核心观点",
            "kb_type": "social_skills",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown"] is False
    assert payload["confidence"] > 0
    assert payload["citations"]
    assert payload["citations"][0]["source"] == "Synthetic demo knowledge base"
    assert "课堂" in payload["answer"] or "核心观点" in payload["answer"]


@pytest.mark.anyio
async def test_safety_policy_knowledge_can_retrieve(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "crisis 自伤 自杀 响应 怎么处理",
            "kb_type": "safety_policy",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown"] is False
    assert payload["citations"]
    titles = {citation["title"] for citation in payload["citations"]}
    assert "Crisis Response Policy" in titles or "Risk Levels Policy" in titles


@pytest.mark.anyio
async def test_unknown_query_returns_unknown_true(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "量子编译器 火星土壤采样 轨道力学",
            "kb_type": "social_skills",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown"] is True
    assert payload["confidence"] == 0.0
    assert payload["citations"] == []
    assert "我不知道" in payload["answer"]


@pytest.mark.anyio
async def test_citations_are_not_empty_for_known_query(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "CBT 自动想法 情绪 强度 替代想法",
            "kb_type": "social_skills",
        },
    )

    payload = response.json()
    assert payload["unknown"] is False
    assert len(payload["citations"]) >= 1
    for citation in payload["citations"]:
        assert citation["title"]
        assert citation["source"] == "Synthetic demo knowledge base"
        assert citation["snippet"]


@pytest.mark.anyio
async def test_knowledge_response_does_not_create_fake_contacts(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "学校心理中心 电话 热线 联系方式",
            "kb_type": "safety_policy",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    text = f"{payload['answer']} {' '.join(c['snippet'] for c in payload['citations'])}"
    assert "Synthetic demo knowledge base" not in payload["answer"]
    assert "本 demo 知识库不包含任何真实热线" in text or payload["unknown"] is True
    assert re.search(r"\b\d{3,4}-\d{7,8}\b", text) is None
    assert re.search(r"\b1[3-9]\d{9}\b", text) is None
    assert "12345" not in text

