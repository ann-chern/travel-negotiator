"""Load and select specialized agent instructions."""

from app.agent.skills.loader import load_skills
from app.agent.skills.router import SkillRouter

__all__ = ["SkillRouter", "load_skills"]
