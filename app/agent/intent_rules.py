"""Small, auditable rules for high-confidence Russian travel intents.

Only explicit messages are recognized. Ambiguous phrases stay with the LLM.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.agent.date_intent import recognize_window
from app.constants.agent import DESTINATION_ALIASES, NUMBER_WORDS
from app.constants.patterns import (
    ADULT_COUNT_PATTERN,
    BUDGET_PATTERN,
    DATE_RANGE_PATTERN,
    DATE_SHIFT_PATTERN,
    DURATION_CONTEXT_PATTERN,
    DURATION_PATTERN,
    FLIGHT_DEPARTURE_WINDOW_PATTERN,
    MOSCOW_ORIGIN_PATTERN,
    STATED_DURATION_PATTERN,
    TRIP_CONTEXT_PATTERN,
    WHITESPACE_PATTERN,
)
from app.schemas.request import TripRequest


def _destination_matches_text(text: str) -> list[tuple[str, str]]:
    from app.data.catalog import active_cities

    found = []
    for city in active_cities():
        patterns = DESTINATION_ALIASES.get(city.id)
        if not patterns:
            # Inflected forms of most city names can be resolved using the
            # canonical catalog name, leaving unknown variants to the LLM.
            stem = re.escape(city.name.casefold())
            patterns = (re.compile(rf"(?<!\w){stem}(?:е|а|у|ом|и|ю)?(?!\w)", re.I),)
        if any(pattern.search(text) for pattern in patterns):
            found.append((city.id, city.name))
    return found


def explicit_destination(text: str) -> str | None:
    hits = _destination_matches_text(text)
    if len(hits) == 1:
        return hits[0][1]
    if not hits and DESTINATION_ALIASES["japan"][0].search(text):
        return "Япония"
    return None


def trip_interval(
    text: str, *, pending_question: str | None = None, today: date | None = None
) -> tuple[date, date, int] | None:
    """Recognize an explicit trip stay, not a flight departure *window*.

    A stand-alone range in response to a question about nights means a stay.
    A Russian phrase meaning "departure from November 5 to 7" remains a
    departure window.
    """
    normalized = text.lower().replace("ё", "е")
    if FLIGHT_DEPARTURE_WINDOW_PATTERN.search(normalized):
        return None
    focus_on_nights = pending_question is not None and "ноч" in pending_question.lower()
    explicit_trip = bool(TRIP_CONTEXT_PATTERN.search(normalized))
    if not (focus_on_nights or explicit_trip):
        return None
    if not DATE_RANGE_PATTERN.search(normalized):
        return None
    window = recognize_window(text, today=today)
    if window is None or window.earliest_departure == window.latest_departure:
        return None
    days = (window.latest_departure - window.earliest_departure).days
    # An explicit 7-day trip and a 30-day range cannot describe one stay.
    # Treat the range as flexible departure dates instead.
    stated = STATED_DURATION_PATTERN.search(normalized)
    if stated and int(stated.group(1)) != days:
        return None
    if days < 1 or days > 60:
        return None
    return window.earliest_departure, window.latest_departure, days


def shift_dates(text: str, request: TripRequest) -> TripRequest | None:
    """Move concrete dates or a departure window; retain all other constraints."""
    normalized = WHITESPACE_PATTERN.sub(" ", text.strip().lower().replace("ё", "е"))
    match = DATE_SHIFT_PATTERN.fullmatch(normalized)
    if not match:
        return None
    days = NUMBER_WORDS.get(match.group(1))
    days = int(match.group(1)) if days is None else days
    delta = timedelta(days=days if match.group(2) == "позже" else -days)
    data = request.model_dump(mode="python")
    if request.departure_date:
        data["departure_date"] = request.departure_date + delta
        if request.return_date:
            data["return_date"] = request.return_date + delta
    elif request.date_window:
        data["date_window"] = {
            "earliest_departure": request.date_window.earliest_departure + delta,
            "latest_departure": request.date_window.latest_departure + delta,
        }
    else:
        return None
    return TripRequest.model_validate(data)


def unsupported_destination_message(request: TripRequest) -> str | None:
    destination = request.destination
    if not destination:
        return None
    from app.data.catalog import active_cities

    cities = active_cities()
    matched = _destination_matches_text(destination)
    if matched or destination.casefold() in {city.id.casefold() for city in cities}:
        return None
    available = ", ".join(city.name for city in cities)
    return (
        f"Направление «{destination}» сейчас отсутствует в демонстрационном каталоге. "
        f"Доступные направления: {available}. Все предложения синтетические. "
        "Могу подобрать доступную альтернативу или помочь пересмотреть направление."
    )


def reinforce_explicit_facts(text: str, request: TripRequest) -> TripRequest:
    """Protect high-confidence facts in user message from omissions by LLM parser.

    Only applies exact supported geographical names and unambiguous Russian patterns.
    Does not infer missing data or silently relax constraints.
    """
    from decimal import Decimal

    from app.schemas.request import Budget, Travelers

    data = request.model_dump(mode="python")
    dest = explicit_destination(text)
    if dest:
        data["destination"] = dest
    if MOSCOW_ORIGIN_PATTERN.search(text):
        data["origin"] = "Москва"
    match = ADULT_COUNT_PATTERN.search(text.lower().replace("ё", "е"))
    if match:
        number = NUMBER_WORDS.get(match.group(1).lower())
        number = int(match.group(1)) if number is None else number
        data["travelers"] = Travelers.model_validate(
            {**request.travelers.model_dump(mode="python"), "adults": number}
        )
    budget = BUDGET_PATTERN.search(text)
    if budget:
        amount = int(WHITESPACE_PATTERN.sub("", budget.group(1)))
        if budget.group(2).lower().startswith(("тыс", "тысяч")):
            amount *= 1000
        data["budget"] = Budget(
            amount=Decimal(amount),
            currency="RUB",
            scope="whole_trip",
            is_hard_limit=True,
        )
    return TripRequest.model_validate(data)


def destination_matches(request_destination: str | None, offer_destination: str | None) -> bool:
    """Enforce an explicitly chosen destination against the active inventory."""
    if not request_destination:
        return True
    from app.data.catalog import active_cities

    wanted = request_destination.strip().casefold()
    for city in active_cities():
        if wanted in (city.id.casefold(), city.name.casefold()):
            return city.id == (offer_destination or "").strip().casefold()
    matches = _destination_matches_text(request_destination)
    return len(matches) == 1 and matches[0][0] == (offer_destination or "").strip().casefold()


def reinforce_trip_duration(text: str, request: TripRequest) -> TripRequest:
    """Recognize an explicit duration when the LLM omits it.

    Day counts in travel requests are treated as nights in this demo, and
    should be described transparently in the UI.
    """
    normalized = text.lower().replace("ё", "е")
    m = DURATION_CONTEXT_PATTERN.search(normalized)
    if m is None:
        m = DURATION_PATTERN.search(normalized)
    if not m:
        return request
    count = NUMBER_WORDS.get(m.group(1))
    if count is None:
        count = int(m.group(1))
    if not 1 <= count <= 60:
        return request
    values = request.model_dump(mode="python")
    values["nights"] = count
    return TripRequest.model_validate(values)
