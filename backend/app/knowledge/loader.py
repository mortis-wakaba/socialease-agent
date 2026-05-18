"""Markdown loader for local knowledge-base documents."""

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

        return [self._load_file(path, kb_type) for path in sorted(kb_dir.rglob("*.md")) if path.is_file()]

    def _load_file(self, path: Path, kb_type: KnowledgeBaseType) -> KnowledgeDocument:
        raw = path.read_text(encoding="utf-8")
        metadata, content = self._parse_frontmatter(raw)
        return KnowledgeDocument(
            title=metadata.get("title", path.stem),
            source_name=metadata.get("source_name", metadata.get("source", "Unknown source")),
            source_type=metadata.get("source_type", "unknown"),
            source_url=metadata.get("source_url"),
            doc_type=metadata.get("doc_type", metadata.get("type", "unknown")),
            kb_type=KnowledgeBaseType(metadata.get("kb_type", kb_type.value)),
            audience=metadata.get("audience", "user_facing"),
            review_status=metadata.get("review_status", "draft"),
            last_reviewed=metadata.get("last_reviewed"),
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
