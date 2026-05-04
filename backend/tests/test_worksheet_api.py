"""API tests for CBT-style worksheet creation."""

import httpx
import pytest

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for worksheet API tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_worksheet_create_complete_input(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": "demo_user",
            "message": (
                "情境：明天课堂发言。自动想法：我肯定会说错被大家笑。"
                "情绪：焦虑。强度：7/10。支持证据：之前发言卡过壳。"
                "反对证据：上次小组讨论同学认真听我说完。替代想法：我可能会紧张，"
                "但可以先说核心观点。下一步：今晚练习开场两遍。"
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is False
    assert payload["missing_fields"] == []
    fields = payload["worksheet"]["fields"]
    assert fields["situation"] == "明天课堂发言"
    assert fields["emotion"] == "焦虑"
    assert fields["emotion_intensity"] == 7
    assert payload["worksheet"]["worksheet_id"]
    assert payload["worksheet"]["citations"]
    assert payload["worksheet"]["missing_fields"] == []


@pytest.mark.anyio
async def test_worksheet_create_insufficient_input(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": "demo_user",
            "message": "我明天要课堂发言，有点紧张。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is False
    assert "automatic_thought" in payload["missing_fields"]
    assert payload["gentle_followup_questions"]
    assert payload["worksheet"]["missing_fields"] == payload["missing_fields"]
    assert (
        payload["worksheet"]["gentle_followup_questions"]
        == payload["gentle_followup_questions"]
    )
    assert payload["worksheet"]["fields"]["emotion"] == "紧张"


@pytest.mark.anyio
async def test_worksheet_create_crisis_input_is_blocked(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": "demo_user",
            "message": "我不想活了，想伤害自己。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is True
    assert payload["worksheet"] is None
    assert payload["safety_result"]["risk_level"] == "crisis"
    assert "暂停自助练习" in payload["response"]


@pytest.mark.anyio
async def test_worksheet_create_high_risk_input_keeps_safety_result(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": "demo_user",
            "message": "情境：宿舍沟通。室友一直霸凌我，我很害怕。情绪：害怕。强度：8。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is False
    assert payload["safety_result"]["risk_level"] == "high"
    assert payload["worksheet"]["citations"]
    assert "专业心理支持" in payload["disclaimer"]


@pytest.mark.anyio
async def test_worksheet_disclaimer_is_non_medical(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": "demo_user",
            "message": "情境：社团破冰。情绪：尴尬。强度：5。",
        },
    )

    assert response.status_code == 200
    disclaimer = response.json()["disclaimer"]
    assert "自助反思练习" in disclaimer
    assert "不用于判断疾病" in disclaimer
    assert "不能替代专业心理支持" in disclaimer
    assert "一定" not in disclaimer


@pytest.mark.anyio
async def test_worksheet_create_returns_citations(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": "demo_user",
            "message": "情境：小组讨论。自动想法：我会说不好。情绪：担心。强度：6。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    citations = payload["worksheet"]["citations"]
    assert citations
    assert any(citation["title"] == "CBT Style Reflection Guide" for citation in citations)
    assert all(citation["source"] == "Synthetic demo knowledge base" for citation in citations)


@pytest.mark.anyio
async def test_worksheet_get_returns_saved_record(client: httpx.AsyncClient) -> None:
    create_response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": "demo_user",
            "message": "情境：小组讨论。情绪：担心。强度：4。下一步：先说一句观点。",
        },
    )
    worksheet_id = create_response.json()["worksheet"]["worksheet_id"]

    response = await client.get(f"/api/worksheet/{worksheet_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worksheet_id"] == worksheet_id
    assert payload["user_id"] == "demo_user"
    assert "citations" in payload
    assert "missing_fields" in payload
    assert "gentle_followup_questions" in payload
