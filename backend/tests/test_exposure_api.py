"""API tests for graded exposure planning."""

import httpx
import pytest

from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for exposure API tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


async def create_plan(client: httpx.AsyncClient, user_id: str) -> dict:
    """Create a plan and return its JSON payload."""
    response = await client.post(
        "/api/exposure/plan",
        json={
            "user_id": user_id,
            "target_scenario": "课堂发言",
            "current_anxiety_level": 7,
            "previous_attempts": ["写过开场白"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is False
    assert payload["safety_result"]["risk_level"] == "low"
    return payload["plan"]


@pytest.mark.anyio
async def test_exposure_plan_creates_ladder(client: httpx.AsyncClient) -> None:
    plan = await create_plan(client, "exposure_plan_user")

    assert plan["user_id"] == "exposure_plan_user"
    assert plan["target_scenario"] == "课堂发言"
    assert len(plan["tasks"]) == 6
    difficulties = [task["difficulty"] for task in plan["tasks"]]
    assert difficulties == sorted(difficulties)
    assert all(1 <= difficulty <= 10 for difficulty in difficulties)
    assert plan["recommended_next_task_id"] == plan["tasks"][0]["task_id"]
    assert "社交练习的分级计划" in plan["disclaimer"]
    assert "不用于判断疾病" in plan["disclaimer"]
    assert "不能替代专业心理支持" in plan["disclaimer"]


@pytest.mark.anyio
async def test_exposure_plan_tasks_include_citations(
    client: httpx.AsyncClient,
) -> None:
    plan = await create_plan(client, "exposure_citations_user")

    assert plan["tasks"][0]["citations"]
    titles = {
        citation["title"]
        for task in plan["tasks"]
        for citation in task["citations"]
    }
    assert "Exposure Training Practice Guide" in titles


@pytest.mark.anyio
async def test_exposure_completed_with_lower_anxiety_increases_difficulty(
    client: httpx.AsyncClient,
) -> None:
    plan = await create_plan(client, "exposure_completed_user")
    task = plan["tasks"][2]

    response = await client.post(
        "/api/exposure/complete",
        json={
            "user_id": "exposure_completed_user",
            "task_id": task["task_id"],
            "status": "completed",
            "anxiety_before": 7,
            "anxiety_after": 4,
            "reflection": "完成后发现比想象中可控。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_task"]["difficulty"] >= task["difficulty"]
    assert "raises difficulty" in payload["adjustment_reason"]
    assert payload["plan"]["attempts"][0]["status"] == "completed"


@pytest.mark.anyio
async def test_exposure_too_hard_lowers_difficulty(client: httpx.AsyncClient) -> None:
    plan = await create_plan(client, "exposure_too_hard_user")
    task = plan["tasks"][4]

    response = await client.post(
        "/api/exposure/complete",
        json={
            "user_id": "exposure_too_hard_user",
            "task_id": task["task_id"],
            "status": "too_hard",
            "anxiety_before": 8,
            "anxiety_after": 9,
            "reflection": "太难了，没法完成。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_task"]["difficulty"] < task["difficulty"]
    assert "lowers" in payload["adjustment_reason"]


@pytest.mark.anyio
async def test_exposure_skipped_gives_smaller_task(client: httpx.AsyncClient) -> None:
    plan = await create_plan(client, "exposure_skipped_user")
    task = plan["tasks"][3]

    response = await client.post(
        "/api/exposure/complete",
        json={
            "user_id": "exposure_skipped_user",
            "task_id": task["task_id"],
            "status": "skipped",
            "anxiety_before": 6,
            "anxiety_after": 6,
            "reflection": "当时没开始。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_task"]["difficulty"] <= task["difficulty"]
    assert "smaller" in payload["adjustment_reason"]


@pytest.mark.anyio
async def test_get_user_exposure_returns_current_plan(
    client: httpx.AsyncClient,
) -> None:
    await create_plan(client, "exposure_progress_user")

    response = await client.get("/api/users/exposure_progress_user/exposure")

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "exposure_progress_user"
    assert payload["plan"]["target_scenario"] == "课堂发言"


@pytest.mark.anyio
async def test_exposure_plan_crisis_input_is_blocked(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/exposure/plan",
        json={
            "user_id": "exposure_crisis_user",
            "target_scenario": "我不想活了，想伤害自己",
            "current_anxiety_level": 9,
            "previous_attempts": [],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked"] is True
    assert payload["plan"] is None
    assert payload["safety_result"]["risk_level"] == "crisis"
    assert "暂停社交练习计划" in payload["response"]
