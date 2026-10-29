"""Priority-based clarification plan, separate from LLM wording.

Only facts in TripRequest are used. A missing destination is not mandatory:
search across destinations is valid. Never infer a date or an adult count.
"""

from __future__ import annotations

from app.constants.clarifications import (
    ADULTS_CLARIFICATION,
    CHILDREN_AGES_CLARIFICATION,
    DEPARTURE_DATE_CLARIFICATION,
    NIGHTS_CLARIFICATION,
    ORIGIN_CLARIFICATION,
)
from app.schemas.agent import Clarification
from app.schemas.request import TripRequest


def clarification_plan(request: TripRequest) -> tuple[Clarification, ...]:
    """One question per essential blocker, in fixed priority order.

    Unresolved, explicitly ambiguous requirements block recommendations;
    unsupported-by-catalog issues are NOT silently discarded but are handled
    by the normal search/negotiation path, which may return unsupported.
    """
    questions: list[Clarification] = []
    if not request.origin:
        questions.append(ORIGIN_CLARIFICATION)
    if request.departure_date is None and request.date_window is None:
        questions.append(DEPARTURE_DATE_CLARIFICATION)
    if request.nights is None:
        questions.append(NIGHTS_CLARIFICATION)
    if request.travelers.adults is None:
        questions.append(ADULTS_CLARIFICATION)
    if request.travelers.children_ages_unknown:
        questions.append(CHILDREN_AGES_CLARIFICATION)
    for idx, item in enumerate(request.unresolved):
        if item.reason in ("ambiguous", "missing_detail"):
            questions.append(
                Clarification(
                    code=f"unresolved_{idx}",
                    question=f"Уточни, пожалуйста, что ты имеешь в виду: «{item.text}»?",
                    missing_label=item.text,
                    reason=item.reason,
                )
            )
    return tuple(questions)


def select_clarification(request: TripRequest) -> Clarification | None:
    plan = clarification_plan(request)
    return plan[0] if plan else None
