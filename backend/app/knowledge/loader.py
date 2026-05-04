"""Markdown loader for demo knowledge-base documents."""

from pathlib import Path

from app.models_knowledge import KnowledgeBaseType, KnowledgeDocument


class MarkdownKnowledgeLoader:
    """Load markdown files with simple frontmatter metadata."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (
            Path(__file__).resolve().parents[2] / "data" / "knowledge_base"
        )

    def load(self, kb_type: KnowledgeBaseType) -> list[KnowledgeDocument]:
        """Load all markdown documents for a knowledge-base type."""
        kb_dir = self.base_dir / kb_type.value
        if not kb_dir.exists():
            return []

        return [
            self._load_file(path)
            for path in sorted(kb_dir.glob("*.md"))
            if path.is_file()
        ]

    def _load_file(self, path: Path) -> KnowledgeDocument:
        raw = path.read_text(encoding="utf-8")
        metadata, content = self._parse_frontmatter(raw)
        return KnowledgeDocument(
            title=metadata.get("title", path.stem),
            source=metadata.get("source", "Unknown source"),
            doc_type=metadata.get("type", "unknown"),
            path=str(path),
            content=content.strip(),
        )

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
        if not raw.startswith("---"):
            return {}, raw

        parts = raw.split("---", 2)
        if len(parts) < 3:
            return {}, raw

        metadata: dict[str, str] = {}
        for line in parts[1].splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
        return metadata, parts[2]

