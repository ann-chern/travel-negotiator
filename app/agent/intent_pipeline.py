"""Typed planning decisions: one calendar interpretation and one clarification policy.

The LLM extracts open-ended facts, this module resolves calendar semantics and
prevents absent values from being confused with flexible values. It never invents
supplier inventory, booking availability, or prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
import re

from app.agent.date_intent import recognize_window
from app.schemas.request import DateWindow, TripRequest


class DateKind(str, Enum):
    UNCHANGED = 'unchanged'
    EXACT_STAY = 'exact_stay'
    FLEXIBLE_DEPARTURE = 'flexible_departure'


class DecisionKind(str, Enum):
    SEARCH = 'search'
    CLARIFY = 'clarify'
    CATALOG_GAP = 'catalog_gap'


@dataclass(frozen=True)
class DateInterpretation:
    kind: DateKind
    window: DateWindow | None = None
    departure: date | None = None
    return_date: date | None = None
    nights: int | None = None


@dataclass(frozen=True)
class PlanningDecision:
    kind: DecisionKind
    question: str | None = None
    missing_field: str | None = None


def interpret_calendar(message: str, *, pending_question: str | None = None,
                       today: date | None = None) -> DateInterpretation:
    """Interpret month ranges contextually, without reducing a flexible month to one day.

    Date parser handles date syntax; LLM remains responsible for novel semantic
    expressions. A trip interval and a departure window are deliberately distinct.
    """
    from app.agent.intent_rules import trip_interval
    stay = trip_interval(message, pending_question=pending_question, today=today)
    if stay:
        start, end, nights = stay
        return DateInterpretation(DateKind.EXACT_STAY, departure=start,
                                  return_date=end, nights=nights)
    window = recognize_window(message, today=today)
    if window:
        return DateInterpretation(DateKind.FLEXIBLE_DEPARTURE, window=window)
    return DateInterpretation(DateKind.UNCHANGED)


def apply_calendar(request: TripRequest, interpretation: DateInterpretation) -> TripRequest:
    if interpretation.kind == DateKind.UNCHANGED:
        return request
    data = request.model_dump(mode='python')
    if interpretation.kind == DateKind.EXACT_STAY:
        data.update(departure_date=interpretation.departure,
                    return_date=interpretation.return_date,
                    nights=interpretation.nights, date_window=None)
    else:
        data.update(date_window=interpretation.window, departure_date=None,
                    return_date=None)
    return TripRequest.model_validate(data)


def decide_next(request: TripRequest, *, available_destinations: frozenset[str] | None = None) -> PlanningDecision:
    """Ask only for hard blockers. Destination and budget are optional.

    Prefer a broad, usable date interval over any question about an exact day.
    The actual catalog must enforce its coverage and avoid fabricated results.
    """
    from app.agent.clarification import select_clarification
    from app.agent.intent_rules import unsupported_destination_message
    if unsupported_destination_message(request):
        return PlanningDecision(DecisionKind.CATALOG_GAP)
    focus = select_clarification(request)
    if focus:
        return PlanningDecision(DecisionKind.CLARIFY, focus.question, focus.code)
    return PlanningDecision(DecisionKind.SEARCH)


def apply_travel_goal(request: TripRequest, goal: str | None) -> TripRequest:
    """Store intent as a SOFT preference; never silently turn a desire into a hard filter.

    The existing optimizer scores matching destination tags. No weather claim is
    made: beach-friendly geography does not establish swimming conditions.
    """
    if goal is None:
        return request
    from app.schemas.request import Preference, Importance, Source
    # Translate the intent taxonomy into existing catalog destination tags.
    tags = {"beach": "beach", "culture": "culture", "nature": "nature",
            "city": "city", "family": "family", "relax": "family"}
    tag = tags.get(goal)
    if tag is None:
        return request
    prefs = [p for p in request.preferences if p.field != "destination.tags"]
    prefs.append(Preference(field="destination.tags", desired_value=tag,
                            importance=Importance.HIGH, source=Source.EXPLICIT))
    return TripRequest.model_validate({**request.model_dump(mode="python"), "preferences": prefs})
