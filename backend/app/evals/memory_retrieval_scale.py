"""Deterministic large-corpus fixtures for episodic-memory retrieval evals."""

from itertools import product

from app.evals.loader import load_memory_scale_seeds
from app.evals.models import (
    MemoryRetrievalEvalCase,
    MemoryRetrievalFixture,
)
from app.models_memory_types import MemoryType


SCALE_USER_ID = "eval_scale_user"
SCALE_BACKGROUND_MEMORY_COUNT = 2048
SCALE_QUERY_CASE_COUNT = 36

_CONTEXTS = (
    ("课堂讨论", "classroom_speech"),
    ("小组协作", "group_discussion"),
    ("宿舍沟通", "dorm_conflict"),
    ("面试交流", "interview_self_intro"),
    ("社团分工", "club_workload"),
    ("活动破冰", "club_icebreaking"),
    ("项目汇报", "presentation_qa"),
    ("线上协作", "online_project_checkin"),
    ("教师答疑", "office_hours"),
    ("校园活动", "networking"),
    ("同伴反馈", "peer_feedback"),
    ("关系修复", "conflict_repair"),
    ("课程展示", "course_presentation"),
    ("任务协调", "task_coordination"),
    ("初次见面", "first_meeting"),
    ("请求帮助", "ask_for_help"),
)
_OPENINGS = (
    "说明这次交流的主题",
    "复述已经确认的信息",
    "提出一个具体问题",
    "用一句话概括自己的想法",
    "确认双方可用的时间",
    "描述可观察到的情况",
    "说明自己能完成的部分",
    "询问对方目前的安排",
    "回到预先写下的关键词",
    "表达对对方观点的理解",
    "提出一个可协商的请求",
    "给自己留出短暂停顿",
    "确认共同目标",
    "列出两个可选方案",
    "总结上一轮的结论",
    "说明希望得到的支持",
)
_FOLLOW_UPS = (
    "邀请对方补充",
    "确认下一步",
    "给出一个简短理由",
    "询问是否需要调整",
    "留出回应时间",
    "约定之后再核对",
    "把请求说得更具体",
    "用自己的话做总结",
)


def build_scale_retrieval_cases() -> list[MemoryRetrievalEvalCase]:
    """Expand reviewed semantic seeds into three paraphrase cases each."""
    cases: list[MemoryRetrievalEvalCase] = []
    for seed in load_memory_scale_seeds():
        for query_index, query in enumerate(seed.queries, start=1):
            target_id = f"scale_{seed.id}_target_{query_index}"
            memories = [
                MemoryRetrievalFixture(
                    memory_id=target_id,
                    user_id=SCALE_USER_ID,
                    memory_type=MemoryType.HELPFUL_STRATEGY,
                    summary=seed.target_summary,
                    scenario_type=seed.scenario_type,
                    occurred_days_ago=300,
                )
            ]
            forbidden_ids: list[str] = []
            for negative_index, summary in enumerate(
                seed.hard_negative_summaries,
                start=1,
            ):
                memory_id = (
                    f"scale_{seed.id}_negative_{query_index}_{negative_index}"
                )
                forbidden_ids.append(memory_id)
                memories.append(
                    MemoryRetrievalFixture(
                        memory_id=memory_id,
                        user_id=SCALE_USER_ID,
                        memory_type=MemoryType.HELPFUL_STRATEGY,
                        summary=summary,
                        scenario_type=seed.scenario_type,
                        occurred_days_ago=negative_index,
                    )
                )
            cases.append(
                MemoryRetrievalEvalCase(
                    id=f"scale_{seed.id}_q{query_index}",
                    category="scale_semantic_relevance",
                    user_id=SCALE_USER_ID,
                    query=query,
                    scenario_type=seed.scenario_type,
                    allowed_memory_types=[MemoryType.HELPFUL_STRATEGY],
                    memories=memories,
                    expected_memory_ids=[target_id],
                    forbidden_memory_ids=forbidden_ids,
                    demo=True,
                )
            )
    if len(cases) != SCALE_QUERY_CASE_COUNT:
        raise RuntimeError(
            "scale seed count changed without updating SCALE_QUERY_CASE_COUNT"
        )
    return cases


def build_scale_background_memories(
    *,
    user_id: str,
) -> list[MemoryRetrievalFixture]:
    """Build a repeatable, diverse pool that exceeds the runtime SQL window."""
    fixtures: list[MemoryRetrievalFixture] = []
    combinations = product(_CONTEXTS, _OPENINGS, _FOLLOW_UPS)
    for index, ((context, scenario_type), opening, follow_up) in enumerate(
        combinations
    ):
        if index >= SCALE_BACKGROUND_MEMORY_COUNT:
            break
        fixtures.append(
            MemoryRetrievalFixture(
                memory_id=f"scale_background_{user_id}_{index:04d}",
                user_id=user_id,
                memory_type=MemoryType.HELPFUL_STRATEGY,
                summary=f"在{context}时，先{opening}，再{follow_up}。",
                scenario_type=scenario_type,
                occurred_days_ago=(index % 180) + 1,
            )
        )
    if len(fixtures) != SCALE_BACKGROUND_MEMORY_COUNT:
        raise RuntimeError("scale background generator produced an incomplete pool")
    return fixtures
