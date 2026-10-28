"""Priority-based clarification plan, separate from LLM wording.

Only facts in TripRequest are used. A missing destination is not mandatory:
search across destinations is valid. Never infer a date or an adult count.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.request import TripRequest


@dataclass(frozen=True)
class Clarification:
    code: str
    question: str
    missing_label: str
    reason: str


def clarification_plan(request: TripRequest) -> tuple[Clarification, ...]:
    """One question per essential blocker, in fixed priority order.

    Unresolved, explicitly ambiguous requirements block recommendations;
    unsupported-by-catalog issues are NOT silently discarded but are handled
    by the normal search/negotiation path, which may return unsupported.
    """
    questions: list[Clarification] = []
    if not request.origin:
        questions.append(Clarification('origin', 'Из какого города планируете выезжать?',
                                       'город отправления', 'required'))
    if request.departure_date is None and request.date_window is None:
        questions.append(Clarification('departure_date',
                                       'Когда хотите выехать? Можно указать точную дату или период до 31 дня.',
                                       'дату или период отправления', 'required'))
    if request.nights is None:
        questions.append(Clarification('nights', 'На сколько ночей планируете поездку?',
                                       'продолжительность поездки', 'required'))
    if request.travelers.adults is None:
        questions.append(Clarification('adults', 'Сколько взрослых поедет?',
                                       'число взрослых', 'required'))
    if request.travelers.children_ages_unknown:
        questions.append(Clarification('children_ages', 'Сколько лет каждому ребёнку на момент поездки?',
                                       'возраст детей', 'missing_detail'))
    for idx, item in enumerate(request.unresolved):
        if item.reason in ('ambiguous', 'missing_detail'):
            questions.append(Clarification(f'unresolved_{idx}',
                             f'Уточни, пожалуйста, что ты имеешь в виду: «{item.text}»?',
                             item.text, item.reason))
    return tuple(questions)


def select_clarification(request: TripRequest) -> Clarification | None:
    plan = clarification_plan(request)
    return plan[0] if plan else None
