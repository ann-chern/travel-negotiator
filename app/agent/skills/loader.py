"""Safely load trusted skill instructions from SKILL.md files."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.schemas.agent import SkillDefinition

SKILLS_DIR = Path(__file__).resolve().parent


def _parse_frontmatter(text: str, source: Path) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"missing YAML frontmatter in {source}")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"unterminated YAML frontmatter in {source}") from exc

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"invalid frontmatter line in {source}: {line!r}")
        metadata[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise ValueError(f"empty skill instructions in {source}")
    return metadata, body


def _load_skill(path: Path) -> SkillDefinition:
    metadata, instructions = _parse_frontmatter(path.read_text(encoding="utf-8"), path)
    name = metadata.get("name", "")
    if name != path.parent.name:
        raise ValueError(f"skill name {name!r} does not match folder {path.parent.name!r}")
    tools = tuple(
        item.strip()
        for item in metadata.get("allowed-tools", "").split(",")
        if item.strip()
    )
    return SkillDefinition(
        name=name,
        description=metadata.get("description", ""),
        tools=tools,
        instructions=instructions,
        source=str(path),
    )


@lru_cache(maxsize=1)
def load_skills(skills_dir: Path = SKILLS_DIR) -> dict[str, SkillDefinition]:
    """Load all skills and reject duplicate names as configuration errors."""
    loaded: dict[str, SkillDefinition] = {}
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        skill = _load_skill(path)
        if skill.name in loaded:
            raise ValueError(f"duplicate skill: {skill.name}")
        loaded[skill.name] = skill
    return loaded
