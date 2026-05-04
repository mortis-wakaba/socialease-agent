"""Rule-based CBT-style worksheet extraction agent."""

import re

from app.models_worksheet import WorksheetFields


class WorksheetAgent:
    """Extract CBT-style worksheet fields without making medical claims."""

    field_labels: dict[str, tuple[str, ...]] = {
        "situation": ("情境", "场景", "situation"),
        "automatic_thought": ("自动想法", "想法", "担心", "automatic_thought"),
        "emotion": ("情绪", "感受", "emotion"),
        "emotion_intensity": ("强度", "焦虑强度", "emotion_intensity"),
        "evidence_for": ("支持证据", "证据支持", "evidence_for"),
        "evidence_against": ("反对证据", "证据反对", "evidence_against"),
        "alternative_thought": ("替代想法", "alternative_thought"),
        "next_action": ("下一步", "行动", "next_action"),
    }
    required_fields: tuple[str, ...] = (
        "situation",
        "automatic_thought",
        "emotion",
        "emotion_intensity",
        "evidence_for",
        "evidence_against",
        "alternative_thought",
        "next_action",
    )
    followup_questions: dict[str, str] = {
        "situation": "可以补充一下具体发生了什么场景吗？",
        "automatic_thought": "当时脑中最强烈的自动想法是什么？",
        "emotion": "你当时最明显的情绪是什么？",
        "emotion_intensity": "如果用 0 到 10 分表示强度，大概是多少？",
        "evidence_for": "有哪些事实让你觉得这个想法可能是真的？",
        "evidence_against": "有没有事实说明事情也可能没那么糟？",
        "alternative_thought": "能不能写一个更平衡、温和的替代想法？",
        "next_action": "接下来一个小而安全的行动可以是什么？",
    }

    def create_fields(self, message: str) -> tuple[WorksheetFields, list[str], list[str]]:
        """Extract worksheet fields and return missing field guidance."""
        values = {
            field: self._extract_labeled_value(message, labels)
            for field, labels in self.field_labels.items()
        }
        self._fill_heuristics(message, values)

        intensity = self._parse_intensity(values.get("emotion_intensity"), message)
        fields = WorksheetFields(
            situation=values.get("situation"),
            automatic_thought=values.get("automatic_thought"),
            emotion=values.get("emotion"),
            emotion_intensity=intensity,
            evidence_for=values.get("evidence_for"),
            evidence_against=values.get("evidence_against"),
            alternative_thought=values.get("alternative_thought"),
            next_action=values.get("next_action"),
        )
        missing = [
            field
            for field in self.required_fields
            if getattr(fields, field) in (None, "")
        ]
        questions = [self.followup_questions[field] for field in missing[:4]]
        return fields, missing, questions

    def _fill_heuristics(self, message: str, values: dict[str, str | None]) -> None:
        if values.get("emotion") is None:
            for emotion in ("焦虑", "紧张", "害怕", "尴尬", "难过", "生气", "担心"):
                if emotion in message:
                    values["emotion"] = emotion
                    break

        if values.get("automatic_thought") is None:
            thought_match = re.search(r"(我[^\n。；;]{2,30}(?:会|是不是|可能|肯定)[^\n。；;]{0,40})", message)
            if thought_match:
                values["automatic_thought"] = thought_match.group(1).strip()

        if values.get("situation") is None:
            for marker in ("在", "当", "今天", "明天"):
                index = message.find(marker)
                if index >= 0:
                    values["situation"] = message[index : index + 40].strip(" ，。；;\n")
                    break

    @staticmethod
    def _extract_labeled_value(message: str, labels: tuple[str, ...]) -> str | None:
        labels_pattern = "|".join(re.escape(label) for label in labels)
        next_labels = (
            "情境|场景|situation|自动想法|想法|担心|automatic_thought|情绪|感受|emotion|"
            "强度|焦虑强度|emotion_intensity|支持证据|证据支持|evidence_for|"
            "反对证据|证据反对|evidence_against|替代想法|alternative_thought|下一步|行动|next_action"
        )
        pattern = (
            rf"(?:{labels_pattern})\s*[:：]\s*(.+?)"
            rf"(?=(?:[。；;\n]\s*)?(?:{next_labels})\s*[:：]|$)"
        )
        match = re.search(pattern, message, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            return None
        return match.group(1).strip(" \n。；;")

    @staticmethod
    def _parse_intensity(value: str | None, message: str) -> int | None:
        source = value or message
        match = re.search(r"(?<!\d)(10|[0-9])(?:\s*/\s*10|分)?", source)
        if match is None:
            return None
        return int(match.group(1))
