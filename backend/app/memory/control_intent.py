"""Deterministically identify requests that must bypass episodic retrieval."""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import json
import re


class MemoryControlIntent(str, Enum):
    """Application-owned memory operations handled outside conversational RAG."""

    DELETE = "delete"
    EXPORT = "export"
    INSPECT = "inspect"
    SETTINGS = "settings"


MEMORY_CONTROL_POLICY_VERSION = "memory-control-v1"


_MEMORY_OBJECT = (
    r"(?:长期记忆|记忆(?:内容|记录)?|练习记录|保存记录|"
    r"已保存(?:的)?(?:内容|信息|练习)?|保存的(?:内容|信息|练习))"
)
_DELETE_ACTION = r"(?:删除|清除|清空|移除|抹掉)"
_EXPORT_ACTION = r"(?:导出|下载)"
_INSPECT_ACTION = r"(?:查看|管理|检查|列出|展示|显示)"
_SETTINGS_MARKERS = (
    "记忆设置",
    "隐私设置",
    "长期记忆设置",
    "保存设置",
)
_DELETE_PATTERNS = (
    re.compile(rf"{_DELETE_ACTION}.{{0,16}}{_MEMORY_OBJECT}"),
    re.compile(rf"{_MEMORY_OBJECT}.{{0,16}}{_DELETE_ACTION}"),
    re.compile(r"(?:忘掉|忘记)(?:我)?(?:刚才|之前|上次)?(?:说|提到|练习|保存)"),
)
_EXPORT_PATTERNS = (
    re.compile(rf"{_EXPORT_ACTION}.{{0,16}}{_MEMORY_OBJECT}"),
    re.compile(rf"{_MEMORY_OBJECT}.{{0,16}}{_EXPORT_ACTION}"),
)
_INSPECT_PATTERNS = (
    re.compile(rf"{_INSPECT_ACTION}.{{0,16}}{_MEMORY_OBJECT}"),
    re.compile(rf"{_MEMORY_OBJECT}.{{0,16}}{_INSPECT_ACTION}"),
    re.compile(r"(?:你|系统).{0,6}(?:记住|保存).{0,8}(?:什么|哪些)"),
)
_CONSENT_PATTERNS = (
    re.compile(r"(?:不要|别再?|停止|关闭|撤销|取消|不同意).{0,12}(?:保存|记录|记住|长期记忆)"),
    re.compile(r"(?:保存|记录|长期记忆).{0,12}(?:关闭|撤销|取消|不同意)"),
)

_FROZEN_POLICY_MANIFESTS = {
    MEMORY_CONTROL_POLICY_VERSION: (
        "94a056778c6e7ba6851338ac258c4f6d18604b9074112a0135d8014159fa0b27"
    ),
}


def classify_memory_control_intent(query: str) -> MemoryControlIntent | None:
    """Return a bounded control intent without interpreting general memory words."""
    normalized = "".join(query.casefold().split())
    if not normalized:
        return None
    if any(marker in normalized for marker in _SETTINGS_MARKERS):
        return MemoryControlIntent.SETTINGS
    if any(pattern.search(normalized) for pattern in _DELETE_PATTERNS):
        return MemoryControlIntent.DELETE
    if any(pattern.search(normalized) for pattern in _EXPORT_PATTERNS):
        return MemoryControlIntent.EXPORT
    if any(pattern.search(normalized) for pattern in _INSPECT_PATTERNS):
        return MemoryControlIntent.INSPECT
    if any(pattern.search(normalized) for pattern in _CONSENT_PATTERNS):
        return MemoryControlIntent.SETTINGS
    return None


def should_bypass_episodic_retrieval(query: str) -> bool:
    """Return whether conversational episodic retrieval must be skipped."""
    return classify_memory_control_intent(query) is not None


def memory_control_policy_manifest_sha256() -> str:
    """Return a content-free fingerprint of the frozen classifier policy."""
    payload = {
        "version": MEMORY_CONTROL_POLICY_VERSION,
        "memory_object": _MEMORY_OBJECT,
        "delete_action": _DELETE_ACTION,
        "export_action": _EXPORT_ACTION,
        "inspect_action": _INSPECT_ACTION,
        "settings_markers": list(_SETTINGS_MARKERS),
        "delete_patterns": [pattern.pattern for pattern in _DELETE_PATTERNS],
        "export_patterns": [pattern.pattern for pattern in _EXPORT_PATTERNS],
        "inspect_patterns": [pattern.pattern for pattern in _INSPECT_PATTERNS],
        "consent_patterns": [pattern.pattern for pattern in _CONSENT_PATTERNS],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def memory_control_policy_is_frozen() -> bool:
    """Return whether this version still matches its reviewed manifest."""
    return _FROZEN_POLICY_MANIFESTS.get(MEMORY_CONTROL_POLICY_VERSION) == (
        memory_control_policy_manifest_sha256()
    )
