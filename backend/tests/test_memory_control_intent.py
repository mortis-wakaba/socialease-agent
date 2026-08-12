"""Contracts for deterministic memory-control retrieval bypass."""

import pytest

from app.memory.control_intent import (
    MEMORY_CONTROL_POLICY_VERSION,
    MemoryControlIntent,
    classify_memory_control_intent,
    memory_control_policy_is_frozen,
    memory_control_policy_manifest_sha256,
    should_bypass_episodic_retrieval,
)


def test_memory_control_policy_version_is_frozen() -> None:
    assert MEMORY_CONTROL_POLICY_VERSION == "memory-control-v1"
    assert memory_control_policy_manifest_sha256() == (
        "94a056778c6e7ba6851338ac258c4f6d18604b9074112a0135d8014159fa0b27"
    )
    assert memory_control_policy_is_frozen() is True


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("删除上次那个练习的记忆", MemoryControlIntent.DELETE),
        ("在记忆设置里撤销上次练习的保存", MemoryControlIntent.SETTINGS),
        ("不要保存这次练习", MemoryControlIntent.SETTINGS),
        ("导出我的长期记忆", MemoryControlIntent.EXPORT),
        ("查看系统保存了哪些内容", MemoryControlIntent.INSPECT),
        ("你记住了关于我的什么", MemoryControlIntent.INSPECT),
    ],
)
def test_memory_control_intents_are_classified(
    query: str,
    expected: MemoryControlIntent,
) -> None:
    assert classify_memory_control_intent(query) is expected
    assert should_bypass_episodic_retrieval(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "我想练习如何记住课堂发言要点",
        "删除好友后怎么解释比较合适",
        "我的记忆力最近不太好",
        "继续练习在课堂上表达观点",
        "我不想再用那个方法",
    ],
)
def test_semantic_neighbors_do_not_bypass_retrieval(query: str) -> None:
    assert classify_memory_control_intent(query) is None
    assert should_bypass_episodic_retrieval(query) is False
