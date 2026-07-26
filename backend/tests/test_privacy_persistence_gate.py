"""Tests for privacy-aware persistence policy and service integration."""

from uuid import uuid4

import httpx
import pytest

from app.auth.tokens import create_auth_token
from app.main import app
from app.models_llm import LLMUsage
from app.privacy.persistence_gate import PersistenceGate
from app.privacy.policy import PersistenceKind
from app.privacy.redaction import (
    detect_sensitive_categories,
    redact_sensitive_identifiers,
)
from app.services.roleplay_service import roleplay_service

TEST_AUTH_SECRET = "privacy-test-secret"


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Create an async ASGI client for privacy persistence tests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def enable_local_developer_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make raw trace diagnostics explicit in privacy persistence tests."""
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "demo")
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")


def test_persistence_gate_minimizes_raw_message_by_default() -> None:
    gate = PersistenceGate()
    decision = gate.persist_text(
        user_id=f"privacy_gate_default_{uuid4().hex}",
        kind=PersistenceKind.WORKSHEET_SOURCE_MESSAGE,
        text="我的邮箱是 demo@example.com，明天课堂发言很紧张。",
    )

    assert decision.minimized is True
    assert decision.redacted_types == ["email"]
    assert decision.persisted_text == "[raw worksheet source minimized by privacy policy]"
    assert "demo@example.com" not in decision.persisted_text


def test_persistence_gate_minimizes_exposure_target_scenario() -> None:
    gate = PersistenceGate()
    decision = gate.persist_text(
        user_id=f"privacy_gate_exposure_target_{uuid4().hex}",
        kind=PersistenceKind.EXPOSURE_TARGET_SCENARIO,
        text="和李四同学约饭后聊我手机号 13912345678",
    )

    assert decision.minimized is True
    assert decision.persisted_text == "[raw exposure target scenario minimized by privacy policy]"
    assert "李四" not in decision.persisted_text
    assert "13912345678" not in decision.persisted_text


def test_persistence_gate_redacts_session_review_next_step() -> None:
    gate = PersistenceGate()
    raw = "下次和姓名：张三 练习，电话 13912345678，邮箱 review@example.com。"

    decision = gate.persist_text(
        user_id=f"privacy_gate_session_review_{uuid4().hex}",
        kind=PersistenceKind.SESSION_REVIEW_NEXT_STEP,
        text=raw,
    )

    assert decision.minimized is False
    assert decision.policy == "redact_only"
    assert "张三" not in decision.persisted_text
    assert "13912345678" not in decision.persisted_text
    assert "review@example.com" not in decision.persisted_text
    assert set(decision.redacted_types) >= {"person_name", "phone", "email"}


def test_persistence_gate_redacts_derived_and_agent_messages() -> None:
    gate = PersistenceGate()
    worksheet_decision = gate.persist_text(
        user_id=f"privacy_gate_worksheet_field_{uuid4().hex}",
        kind=PersistenceKind.WORKSHEET_FIELD,
        text="下一步：联系我 13912345678，邮箱 field@example.com。",
    )
    agent_decision = gate.persist_text(
        user_id=f"privacy_gate_roleplay_agent_{uuid4().hex}",
        kind=PersistenceKind.ROLEPLAY_AGENT_MESSAGE,
        text="我听到你提到手机号 13912345678，我们可以换成一句泛化表达。",
    )

    assert worksheet_decision.minimized is False
    assert worksheet_decision.policy == "redact_only"
    assert set(worksheet_decision.redacted_types) >= {"phone", "email"}
    assert "13912345678" not in worksheet_decision.persisted_text
    assert "field@example.com" not in worksheet_decision.persisted_text
    assert agent_decision.minimized is False
    assert agent_decision.policy == "redact_only"
    assert agent_decision.redacted_types == ["phone"]
    assert "13912345678" not in agent_decision.persisted_text


def test_persistence_gate_redacts_memory_and_onboarding_fields() -> None:
    gate = PersistenceGate()
    memory_decision = gate.persist_text(
        user_id=f"privacy_gate_memory_preference_{uuid4().hex}",
        kind=PersistenceKind.MEMORY_PREFERENCE,
        text="偏好里不要保存电话 13912345678 和邮箱 memory@example.com。",
    )
    onboarding_decision = gate.persist_text(
        user_id=f"privacy_gate_onboarding_{uuid4().hex}",
        kind=PersistenceKind.ONBOARDING_FIELD,
        text="姓名：张三，地址：北京市海淀区中关村大街27号。",
    )

    assert memory_decision.policy == "redact_only"
    assert set(memory_decision.redacted_types) >= {"phone", "email"}
    assert "13912345678" not in memory_decision.persisted_text
    assert "memory@example.com" not in memory_decision.persisted_text
    assert onboarding_decision.policy == "redact_only"
    assert set(onboarding_decision.redacted_types) >= {"person_name", "address"}
    assert "张三" not in onboarding_decision.persisted_text
    assert "北京市海淀区中关村大街27号" not in onboarding_decision.persisted_text


def test_redactor_detects_common_chinese_sensitive_identifiers() -> None:
    text = (
        "姓名：张三，身份证 110105199001011234，微信号 wx_demo_123，"
        "QQ: 123456789，学号 2020123456，班级：计科2301班，"
        "学校：重庆大学，住址：重庆市沙坪坝区大学城南路1号，"
        "同学：李四，电话 +86 139-1234-5678，邮箱 pii@example.com。"
    )

    redacted, detected = redact_sensitive_identifiers(text)

    assert detected == [
        "email",
        "national_id",
        "phone",
        "wechat",
        "qq",
        "student_id",
        "address",
        "class_group",
        "organization",
        "person_name",
        "third_party_identity",
    ]
    for raw in [
        "张三",
        "110105199001011234",
        "wx_demo_123",
        "123456789",
        "2020123456",
        "计科2301班",
        "重庆大学",
        "重庆市沙坪坝区大学城南路1号",
        "李四",
        "139-1234-5678",
        "pii@example.com",
    ]:
        assert raw not in redacted


def test_redactor_does_not_treat_generic_dorm_scenario_as_an_address() -> None:
    """Scenario language should remain usable without weakening real address redaction."""
    generic = "宿舍沟通时先提出一个具体请求。"
    address = "宿舍：梅园3号楼412室"

    assert "address" not in detect_sensitive_categories(generic)
    assert "address" in detect_sensitive_categories(address)


def test_third_party_redactor_does_not_treat_scenario_words_as_a_name() -> None:
    redacted, detected = redact_sensitive_identifiers("我想练习室友沟通晚上的关灯时间。")

    assert redacted == "我想练习室友沟通晚上的关灯时间。"
    assert "third_party_identity" not in detected


def test_third_party_redactor_handles_explicit_name_introducer() -> None:
    redacted, detected = redact_sensitive_identifiers("我的室友叫张三。")

    assert "张三" not in redacted
    assert "third_party_identity" in detected


def test_trace_output_redacts_chinese_sensitive_identifiers_without_minimizing() -> None:
    gate = PersistenceGate()
    decision = gate.persist_text(
        user_id=f"privacy_gate_trace_output_{uuid4().hex}",
        kind=PersistenceKind.TRACE_OUTPUT,
        text=(
            "联系人：王五，微信号 wx_demo_456，QQ 987654321，"
            "住址：重庆市沙坪坝区大学城北路2号。"
        ),
    )

    assert decision.minimized is False
    assert decision.redacted_types == [
        "wechat",
        "qq",
        "address",
        "person_name",
    ]
    assert "王五" not in decision.persisted_text
    assert "wx_demo_456" not in decision.persisted_text
    assert "987654321" not in decision.persisted_text
    assert "大学城北路2号" not in decision.persisted_text


def test_trace_output_summary_only_in_production_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOCIALEASE_TRACE_OUTPUT_MODE", raising=False)
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    gate = PersistenceGate()
    raw = "你的手机号 13912345678 和姓名：王五 不需要写入 trace，我们只保留练习摘要。"

    decision = gate.persist_text(
        user_id=f"privacy_gate_trace_summary_{uuid4().hex}",
        kind=PersistenceKind.TRACE_OUTPUT,
        text=raw,
    )

    assert decision.minimized is False
    assert decision.summarized is True
    assert decision.policy == "summary_only"
    assert "13912345678" not in decision.persisted_text
    assert "王五" not in decision.persisted_text
    assert raw not in decision.persisted_text
    assert decision.persisted_text.startswith("[assistant output summarized by privacy policy:")


def test_trace_output_can_be_minimized_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOCIALEASE_TRACE_OUTPUT_MODE", "minimized")
    gate = PersistenceGate()

    decision = gate.persist_text(
        user_id=f"privacy_gate_trace_minimized_{uuid4().hex}",
        kind=PersistenceKind.TRACE_OUTPUT,
        text="这是一段不需要保存的助手回复。",
    )

    assert decision.minimized is True
    assert decision.summarized is False
    assert decision.policy == "minimized"
    assert decision.persisted_text == "[assistant output minimized by privacy policy]"


@pytest.mark.anyio
async def test_chat_trace_input_is_minimized_before_persistence(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"privacy_trace_user_{uuid4().hex}"
    response = await client.post(
        "/api/chat",
        json={
            "user_id": user_id,
            "message": "我的邮箱是 trace@example.com，我想练习课堂发言。",
            "context": {},
        },
    )
    run_id = response.json()["run_id"]

    trace_response = await client.get(f"/api/runs/{run_id}")

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["input"] == "[raw chat input minimized by privacy policy]"
    assert "trace@example.com" not in trace["input"]
    assert trace["product_safe"] is True
    assert trace["privacy_summary"]["trace_layer"] == "product_safe"
    input_policy = next(
        field for field in trace["privacy_summary"]["fields"] if field["field"] == "input"
    )
    assert input_policy["persistence_kind"] == "trace_input"
    assert input_policy["minimized"] is True
    assert input_policy["redacted_types"] == ["email"]
    assert input_policy["original_length"] == len("我的邮箱是 trace@example.com，我想练习课堂发言。")
    assert input_policy["persisted_length"] == len(trace["input"])


@pytest.mark.anyio
async def test_trace_output_redacts_sensitive_identifiers(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"privacy_trace_output_user_{uuid4().hex}"
    response = await client.post(
        "/api/chat",
        json={
            "user_id": user_id,
            "message": "我想找学校心理中心资源，电话是 13912345678",
            "context": {},
        },
    )
    run_id = response.json()["run_id"]

    trace_response = await client.get(f"/api/runs/{run_id}")

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert "13912345678" not in trace["input"]
    assert "13912345678" not in trace["output"]
    output_policy = next(
        field for field in trace["privacy_summary"]["fields"] if field["field"] == "output"
    )
    assert output_policy["persistence_kind"] == "trace_output"
    assert output_policy["minimized"] is False
    assert output_policy["summarized"] is False
    assert output_policy["policy"] == "redact_only"


@pytest.mark.anyio
async def test_chat_trace_output_is_summarized_when_configured(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_TRACE_OUTPUT_MODE", "summary_only")
    user_id = f"privacy_trace_summary_user_{uuid4().hex}"
    response = await client.post(
        "/api/chat",
        json={
            "user_id": user_id,
            "message": "我想练习课堂发言，我的邮箱是 trace_summary@example.com",
            "context": {},
        },
    )
    run_id = response.json()["run_id"]
    raw_response = response.json()["response"]

    trace_response = await client.get(f"/api/runs/{run_id}")

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["output"] != raw_response
    assert raw_response not in trace["output"]
    assert trace["output"].startswith("[assistant output summarized by privacy policy:")
    assert trace["privacy_summary"]["raw_output_retained"] is False
    output_policy = next(
        field for field in trace["privacy_summary"]["fields"] if field["field"] == "output"
    )
    assert output_policy["persistence_kind"] == "trace_output"
    assert output_policy["minimized"] is False
    assert output_policy["summarized"] is True
    assert output_policy["policy"] == "summary_only"


@pytest.mark.anyio
async def test_worksheet_source_message_is_minimized_before_persistence(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"privacy_worksheet_user_{uuid4().hex}"
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": user_id,
            "message": "情境：课堂发言。我的邮箱是 sheet@example.com。情绪：紧张。强度：6。",
        },
    )

    worksheet = response.json()["worksheet"]
    assert worksheet["source_message"] == "[raw worksheet source minimized by privacy policy]"
    assert "sheet@example.com" not in worksheet["source_message"]


@pytest.mark.anyio
async def test_worksheet_fields_are_redacted_before_persistence(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"privacy_worksheet_fields_{uuid4().hex}"
    response = await client.post(
        "/api/worksheet/create",
        json={
            "user_id": user_id,
            "message": (
                "情境：姓名：张三 在学校：清华大学课堂发言，电话 13912345678。"
                "自动想法：我会被同学笑。情绪：焦虑。强度：7。"
                "支持证据：之前发言卡过壳。反对证据：老师给过邮箱 field@example.com 鼓励。"
                "替代想法：我可以先说核心观点。"
                "下一步：微信号 wxfield12345 先写开场，地址：北京市海淀区中关村大街27号。"
            ),
        },
    )

    payload = response.json()
    worksheet = payload["worksheet"]
    worksheet_id = worksheet["worksheet_id"]
    serialized_fields = str(worksheet["fields"])
    assert worksheet["source_message"] == "[raw worksheet source minimized by privacy policy]"
    for raw in [
        "张三",
        "清华大学",
        "13912345678",
        "field@example.com",
        "wxfield12345",
        "北京市海淀区中关村大街27号",
    ]:
        assert raw not in serialized_fields

    detail_response = await client.get(f"/api/worksheet/{worksheet_id}")
    export_response = await client.get(f"/api/users/{user_id}/memory/export")
    serialized_detail = str(detail_response.json()["fields"])
    serialized_export = str(export_response.json()["records"]["worksheets"])
    for serialized in [serialized_detail, serialized_export]:
        assert "13912345678" not in serialized
        assert "field@example.com" not in serialized
        assert "wxfield12345" not in serialized
        assert "北京市海淀区中关村大街27号" not in serialized
    assert "[redacted:phone]" in serialized_export
    assert "[redacted:email]" in serialized_export
    assert "[redacted:address]" in serialized_export


@pytest.mark.anyio
async def test_roleplay_user_message_is_minimized_before_persistence(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"privacy_roleplay_user_{uuid4().hex}"
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
            "difficulty": 3,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    response = await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "message": "我想说观点，也可以联系我 phone 13912345678。",
        },
    )

    messages = response.json()["session"]["messages"]
    user_messages = [message for message in messages if message["role"] == "user"]
    assert user_messages[-1]["content"] == "[raw roleplay message minimized by privacy policy]"
    assert "13912345678" not in user_messages[-1]["content"]


@pytest.mark.anyio
async def test_roleplay_agent_message_is_redacted_before_persistence(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = f"privacy_roleplay_agent_{uuid4().hex}"

    async def fake_next_turn(*args, **kwargs):
        return (
            (
                "我会避免复述姓名：王五、手机号 13912345678、"
                "邮箱 agent@example.com 和地址：北京市海淀区中关村大街27号。"
            ),
            LLMUsage(),
        )

    monkeypatch.setattr(roleplay_service.agent, "next_turn", fake_next_turn)
    start_response = await client.post(
        "/api/roleplay/start",
        json={
            "user_id": user_id,
            "scenario_description": "课堂上轮到我发言时练习清楚表达观点",
            "difficulty": 3,
        },
    )
    session_id = start_response.json()["session"]["session_id"]

    response = await client.post(
        "/api/roleplay/message",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "message": "我想练习课堂发言。",
        },
    )

    payload = response.json()
    assert "[redacted:phone]" in payload["response"]
    assert "[redacted:email]" in payload["response"]
    assert "[redacted:person_name]" in payload["response"]
    assert "[redacted:address]" in payload["response"]
    assert "13912345678" not in payload["response"]
    assert "agent@example.com" not in payload["response"]
    assert "王五" not in payload["response"]
    assert "北京市海淀区中关村大街27号" not in payload["response"]
    agent_message = payload["session"]["messages"][-1]
    assert agent_message["role"] == "agent"
    assert agent_message["content"] == payload["response"]

    detail_response = await client.get(
        f"/api/roleplay/{session_id}",
        params={"user_id": user_id},
    )
    export_response = await client.get(f"/api/users/{user_id}/memory/export")
    serialized_detail = str(detail_response.json()["session"]["messages"])
    serialized_export = str(export_response.json()["records"]["roleplay_sessions"])
    for serialized in [serialized_detail, serialized_export]:
        assert "13912345678" not in serialized
        assert "agent@example.com" not in serialized
        assert "王五" not in serialized
        assert "北京市海淀区中关村大街27号" not in serialized
        assert "[redacted:phone]" in serialized
        assert "[redacted:email]" in serialized
        assert "[redacted:person_name]" in serialized
        assert "[redacted:address]" in serialized


@pytest.mark.anyio
async def test_production_trace_output_defaults_to_summary_for_saved_runs(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOCIALEASE_AUTH_MODE", "production")
    monkeypatch.setenv("SOCIALEASE_AUTH_TOKEN_SECRET", TEST_AUTH_SECRET)
    monkeypatch.setenv("SOCIALEASE_ENABLE_DEVELOPER_ENDPOINTS", "true")
    monkeypatch.delenv("SOCIALEASE_TRACE_OUTPUT_MODE", raising=False)
    token = create_auth_token(
        user_id="privacy_prod_trace_user",
        secret=TEST_AUTH_SECRET,
        roles=("developer",),
    )
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        "/api/chat",
        headers=headers,
        json={
            "user_id": "ignored_body_user",
            "message": "我想练习课堂发言。我的电话是 13912345678，姓名：赵敏。",
            "context": {},
        },
    )
    run_id = response.json()["run_id"]

    trace_response = await client.get(f"/api/runs/{run_id}", headers=headers)

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["output"].startswith("[assistant output summarized by privacy policy:")
    assert response.json()["response"] not in trace["output"]
    assert "13912345678" not in trace["output"]
    assert "赵敏" not in trace["output"]
    output_policy = next(
        field for field in trace["privacy_summary"]["fields"] if field["field"] == "output"
    )
    assert output_policy["persistence_kind"] == "trace_output"
    assert output_policy["summarized"] is True
    assert output_policy["policy"] == "summary_only"


@pytest.mark.anyio
async def test_exposure_reflection_and_previous_attempts_are_minimized(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"privacy_exposure_user_{uuid4().hex}"
    plan_response = await client.post(
        "/api/exposure/plan",
        json={
            "user_id": user_id,
            "target_scenario": "课堂发言",
            "current_anxiety_level": 6,
            "previous_attempts": ["之前把手机号 13912345678 写进了草稿"],
        },
    )
    plan = plan_response.json()["plan"]
    task_id = plan["tasks"][0]["task_id"]

    complete_response = await client.post(
        "/api/exposure/complete",
        json={
            "user_id": user_id,
            "task_id": task_id,
            "status": "completed",
            "anxiety_before": 6,
            "anxiety_after": 4,
            "reflection": "完成了，也提到了邮箱 exposure@example.com。",
        },
    )

    updated_plan = complete_response.json()["plan"]
    assert updated_plan["previous_attempts"] == [
        "[raw previous attempt minimized by privacy policy]"
    ]
    assert updated_plan["attempts"][-1]["reflection"] == (
        "[raw exposure reflection minimized by privacy policy]"
    )
    assert updated_plan["target_scenario"] == (
        "[raw exposure target scenario minimized by privacy policy]"
    )
    assert "课堂发言" not in updated_plan["target_scenario"]
    assert "13912345678" not in updated_plan["tasks"][3]["description"]
    assert "exposure@example.com" not in updated_plan["attempts"][-1]["reflection"]


@pytest.mark.anyio
async def test_exposure_target_scenario_is_minimized_in_plan_tasks_and_export(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"privacy_exposure_target_user_{uuid4().hex}"
    raw_target = "和李四同学约饭后聊我的手机号 13912345678"
    response = await client.post(
        "/api/exposure/plan",
        json={
            "user_id": user_id,
            "target_scenario": raw_target,
            "current_anxiety_level": 7,
            "previous_attempts": [],
        },
    )

    plan = response.json()["plan"]
    serialized_plan = str(plan)
    export_response = await client.get(f"/api/users/{user_id}/memory/export")
    serialized_export = str(export_response.json()["records"]["exposure_plans"])

    assert plan["target_scenario"] == "[raw exposure target scenario minimized by privacy policy]"
    assert raw_target not in serialized_plan
    assert "李四" not in serialized_plan
    assert "13912345678" not in serialized_plan
    assert raw_target not in serialized_export
    assert "李四" not in serialized_export
    assert "13912345678" not in serialized_export
