"""Rule-based graded exposure planner for social practice."""

from datetime import datetime, timezone
from uuid import uuid4

from app.models_knowledge import Citation
from app.models_exposure import (
    ExposureAttempt,
    ExposureFeedbackStatus,
    ExposurePlan,
    ExposureTask,
)


class ExposurePlanner:
    """Create and adjust safe, incremental social practice ladders."""

    def create_tasks(
        self,
        target_scenario: str,
        current_anxiety_level: int,
        previous_attempts: list[str],
        citations: list[Citation],
    ) -> list[ExposureTask]:
        """Generate 6 tasks from easier to harder for the target scenario."""
        base = max(1, min(6, current_anxiety_level - 4))
        difficulties = [min(10, base + step) for step in range(6)]
        previous_note = ""
        if previous_attempts:
            previous_note = f" 参考你之前尝试过：{'；'.join(previous_attempts[:2])}。"

        templates = [
            (
                "写下一个最小开场句",
                "只在纸上写一句你可能会说的话，不需要真的说出口。",
                5,
                "完成一句 20 字以内的安全表达。",
                "只写关键词，不写完整句子。",
            ),
            (
                "低声练习一次",
                "独处时把这句话低声说一遍，观察焦虑强度变化。",
                5,
                "说完一次即可，不要求自然。",
                "只在心里默念一遍。",
            ),
            (
                "向熟悉的人试说",
                "找一位可信任的人，说明你在做社交练习，然后试说一句。",
                10,
                "表达出核心意思，对方是否回应不作为成功标准。",
                "把句子发成文字消息。",
            ),
            (
                "做一次简短真实尝试",
                f"在真实的“{target_scenario}”相关场景里完成一个很短的表达。{previous_note}",
                10,
                "完成一句清楚表达，并记录焦虑前后分数。",
                "把真实尝试缩短到一句问候或一句确认。",
            ),
            (
                "加入一个具体理由",
                "在表达核心意思后补充一个简短理由，让对方更容易理解。",
                15,
                "表达包含“我想/我希望/因为”中的一种结构。",
                "只表达核心意思，不补理由。",
            ),
            (
                "完成一次完整练习",
                f"在“{target_scenario}”中完成一段 2-3 句话的表达，并在结束后复盘。",
                20,
                "完成表达、记录焦虑变化，并写下一点经验。",
                "改为完成一段文字草稿。",
            ),
        ]

        return [
            ExposureTask(
                task_id=str(uuid4()),
                title=title,
                description=description,
                difficulty=difficulties[index],
                estimated_time_minutes=minutes,
                success_criteria=criteria,
                fallback_task=fallback,
                citations=citations,
            )
            for index, (title, description, minutes, criteria, fallback) in enumerate(
                templates
            )
        ]

    def create_attempt(
        self,
        task_id: str,
        status: ExposureFeedbackStatus,
        anxiety_before: int,
        anxiety_after: int,
        reflection: str,
    ) -> ExposureAttempt:
        """Create a timestamped attempt record."""
        return ExposureAttempt(
            task_id=task_id,
            status=status,
            anxiety_before=anxiety_before,
            anxiety_after=anxiety_after,
            reflection=reflection,
            created_at=datetime.now(timezone.utc),
        )

    def choose_next_task(
        self,
        plan: ExposurePlan,
        task_id: str,
        status: ExposureFeedbackStatus,
        anxiety_before: int,
        anxiety_after: int,
    ) -> tuple[ExposureTask | None, str]:
        """Choose the next task using simple difficulty adjustment rules."""
        current = self._find_task(plan.tasks, task_id)
        if current is None:
            return None, "Task not found in current exposure plan."

        if status == ExposureFeedbackStatus.TOO_HARD:
            target_difficulty = max(1, current.difficulty - 2)
            reason = "too_hard feedback lowers the next task difficulty."
            return self._nearest_task(plan.tasks, target_difficulty, prefer_lower=True), reason

        if status == ExposureFeedbackStatus.SKIPPED:
            target_difficulty = max(1, current.difficulty - 1)
            reason = "skipped feedback suggests a smaller next task."
            return self._nearest_task(plan.tasks, target_difficulty, prefer_lower=True), reason

        if status == ExposureFeedbackStatus.COMPLETED and anxiety_after < anxiety_before:
            target_difficulty = min(10, current.difficulty + 1)
            reason = "completed feedback with lower anxiety slightly raises difficulty."
            return self._nearest_task(plan.tasks, target_difficulty, prefer_lower=False), reason

        reason = "completed feedback without anxiety decrease keeps difficulty stable."
        return self._nearest_task(plan.tasks, current.difficulty, prefer_lower=True), reason

    @staticmethod
    def _find_task(tasks: list[ExposureTask], task_id: str) -> ExposureTask | None:
        for task in tasks:
            if task.task_id == task_id:
                return task
        return None

    @staticmethod
    def _nearest_task(
        tasks: list[ExposureTask],
        target_difficulty: int,
        prefer_lower: bool,
    ) -> ExposureTask | None:
        if not tasks:
            return None

        def sort_key(task: ExposureTask) -> tuple[int, int]:
            direction_penalty = 0
            if prefer_lower and task.difficulty > target_difficulty:
                direction_penalty = 1
            if not prefer_lower and task.difficulty < target_difficulty:
                direction_penalty = 1
            return (abs(task.difficulty - target_difficulty), direction_penalty)

        return sorted(tasks, key=sort_key)[0]
