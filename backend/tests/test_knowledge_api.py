"""API tests for local knowledge-base retrieval."""

import re

import httpx
import pytest

from app.auth.tokens import create_auth_token
from app.main import app

TEST_AUTH_SECRET = "knowledge-test-secret"


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
    assert payload["citations"][0]["source_name"] == "Project Authored"
    assert payload["citations"][0]["source_type"] == "project_authored"
    assert "课堂" in payload["answer"] or "核心观点" in payload["answer"]


@pytest.mark.anyio
async def test_safety_policy_knowledge_can_retrieve(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
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
        assert citation["source_name"] == "Project Authored"
        assert citation["source_type"] == "project_authored"
        assert citation["snippet"]


@pytest.mark.anyio
async def test_knowledge_response_does_not_create_fake_contacts(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "学校心理中心 电话 热线 联系方式",
            "kb_type": "support_resources",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    text = f"{payload['answer']} {' '.join(c['snippet'] for c in payload['citations'])}"
    assert "Project Authored" not in payload["answer"]
    assert (
        "本 demo 知识库不包含任何真实热线" in text
        or "编造学校心理中心电话" in text
        or "不会编造联系方式" in text
        or payload["unknown"] is True
    )
    assert re.search(r"\b\d{3,4}-\d{7,8}\b", text) is None
    assert re.search(r"\b1[3-9]\d{9}\b", text) is None
    assert "12345" not in text


@pytest.mark.anyio
async def test_support_resources_return_external_public_citations(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "social anxiety CBT self-help public resource",
            "kb_type": "support_resources",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown"] is False
    assert payload["citations"]
    assert all(citation["source_type"] == "external_public" for citation in payload["citations"])
    assert any(citation["source_name"] in {"NIMH", "NHS Inform", "NHS"} for citation in payload["citations"])
    assert all(citation["source_url"] for citation in payload["citations"])


@pytest.mark.anyio
async def test_internal_knowledge_bases_are_blocked_for_public_api(
    client: httpx.AsyncClient,
) -> None:
    for kb_type in ["product_rubrics", "safety_policy"]:
        response = await client.post(
            "/api/knowledge/query",
            json={
                "query": "clarity naturalness assertiveness empathy rubric",
                "kb_type": kb_type,
            },
        )

        assert response.status_code == 403
        assert "Developer endpoints are disabled" in response.json()["detail"]


@pytest.mark.anyio
async def test_product_rubrics_are_queryable_in_developer_mode(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")

    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "clarity naturalness assertiveness empathy rubric",
            "kb_type": "product_rubrics",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown"] is False
    assert payload["citations"][0]["source_type"] == "project_authored"
    assert payload["citations"][0]["title"] == "Roleplay Feedback Rubric"


@pytest.mark.anyio
async def test_public_knowledge_remains_queryable_without_auth_in_production(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)

    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "课堂发言 怎么准备 核心观点",
            "kb_type": "social_skills",
        },
    )

    assert response.status_code == 200
    assert response.json()["unknown"] is False


@pytest.mark.anyio
async def test_internal_knowledge_rejects_ordinary_authenticated_user(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    token = create_auth_token(
        user_id="ordinary_knowledge_user",
        secret=TEST_AUTH_SECRET,
        roles=("user",),
    )

    response = await client.post(
        "/api/knowledge/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "crisis 自伤 自杀 响应 怎么处理",
            "kb_type": "safety_policy",
        },
    )

    assert response.status_code == 403
    assert "Developer access is required" in response.json()["detail"]


@pytest.mark.anyio
async def test_internal_knowledge_allows_developer_role_in_production(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    token = create_auth_token(
        user_id="developer_knowledge_user",
        secret=TEST_AUTH_SECRET,
        roles=("developer",),
    )

    response = await client.post(
        "/api/knowledge/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "crisis 自伤 自杀 响应 怎么处理",
            "kb_type": "safety_policy",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unknown"] is False
    assert payload["citations"]


@pytest.mark.anyio
async def test_removed_demo_campus_knowledge_type_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/knowledge/query",
        json={
            "query": "campus support",
            "kb_type": "campus_resources_demo",
        },
    )

    assert response.status_code == 422
