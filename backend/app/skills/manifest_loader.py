"""On-demand loading for human-readable SocialEase skill manifests."""

from dataclasses import dataclass
from pathlib import Path

from app.skills.base import SkillDescriptor


@dataclass(frozen=True)
class SkillManifest:
    """Loaded markdown manifest for a registered skill."""

    skill_name: str
    path: str
    content: str


def load_skill_manifest(descriptor: SkillDescriptor) -> SkillManifest | None:
    """Load a skill manifest only when callers need detailed skill knowledge."""
    if descriptor.manifest_path is None:
        return None
    path = Path(descriptor.manifest_path)
    if not path.exists():
        return None
    return SkillManifest(
        skill_name=descriptor.name,
        path=str(path),
        content=path.read_text(encoding="utf-8"),
    )
