"""Select a skill deterministically from validated agent state."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.agent.skills.loader import load_skills
from app.schemas.agent import SkillDefinition, SkillName
from app.schemas.request import TripRequest


class SkillRouter:
    def __init__(
        self,
        skills: dict[str, SkillDefinition] | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self.skills = skills if skills is not None else load_skills()
        self.enabled = enabled

    def for_recommendation(
        self,
        request: TripRequest,
        options: list[dict[str, Any]],
    ) -> SkillDefinition | None:
        if not self.enabled or request.destination or not options:
            return None
        return self.skills.get(SkillName.DESTINATION_DISCOVERY.value)

    def for_negotiation(
        self,
        request: TripRequest,
        reasons: Iterable[str],
        compromises: list[dict[str, Any]],
    ) -> SkillDefinition | None:
        if not self.enabled or request.budget is None:
            return None
        budget_gap = any(
            marker in reason.casefold()
            for reason in reasons
            for marker in ("минимальная цена", "бюджет", "budget")
        )
        budget_proposal = any(item.get("kind") == "budget" for item in compromises)
        if not (budget_gap or budget_proposal):
            return None
        return self.skills.get(SkillName.BUDGET_NEGOTIATION.value)
