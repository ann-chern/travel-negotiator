"""Small, auditable rules for high-confidence Russian travel intents.

Only explicit messages are recognized. Ambiguous phrases stay with the LLM.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from app.agent.date_intent import recognize_window
from app.schemas.request import TripRequest

# The active inventory is the single source of truth for supported destinations.
# A small aliases table only handles inflected Russian names not derivable from
# a simple stem; availability must never be maintained as a second allowlist.
DESTINATION_ALIASES = {
    "istanbul": (r"\bстамбул(?:е|ь|а|у|ом)?\b",),
    "antalya": (r"\bанталь(?:я|ю|е|и|ей)\b",),
    "sochi": (r"\bсочи\b",),
    "phuket": (r"\bпхукет(?:е|а|у|ом)?\b",),
    "japan": (r"\bяпони(?:я|ю|и|ей)\b",),
}


def _destination_matches_text(text: str) -> list[tuple[str, str]]:
    from app.data.catalog import active_cities
    found = []
    for city in active_cities():
        patterns = DESTINATION_ALIASES.get(city.id)
        if not patterns:
            # Inflected forms of most city names can be resolved using the
            # canonical catalog name, leaving unknown variants to the LLM.
            stem = re.escape(city.name.casefold())
            patterns = (rf"(?<!\w){stem}(?:е|а|у|ом|и|ю)?(?!\w)",)
        if any(re.search(p, text, re.I) for p in patterns):
            found.append((city.id, city.name))
    return found


def explicit_destination(text: str) -> str | None:
    hits = _destination_matches_text(text)
    if len(hits) == 1:
        return hits[0][1]
    if not hits and re.search(DESTINATION_ALIASES['japan'][0], text, re.I):
        return 'Япония'
    return None


def trip_interval(text: str, *, pending_question: str | None = None, today: date | None = None) -> tuple[date, date, int] | None:
    """Recognize an explicit trip stay, not a flight departure *window*.

    A stand-alone range in response to a question about nights means a stay.
    'вылет с 5 по 7 ноября' remains a departure window.
    """
    normalized = text.lower().replace('ё', 'е')
    if re.search(r'\b(?:вылет|вылететь|отправлени[ея]|выехать|вые(зд|зжа))\s+(?:примерно\s+)?с\s+\d', normalized):
        return None
    focus_on_nights = pending_question is not None and 'ноч' in pending_question.lower()
    explicit_trip = bool(re.search(r'\b(?:хочу|поездк[аиу]|отдых|едем|поехать|путешестви[ея])\b', normalized))
    if not (focus_on_nights or explicit_trip):
        return None
    if not re.search(r'\b(?:с\s+)?\d{1,2}\s*(?:по|[-–—])\s*\d{1,2}\b', normalized):
        return None
    window = recognize_window(text, today=today)
    if window is None or window.earliest_departure == window.latest_departure:
        return None
    days = (window.latest_departure - window.earliest_departure).days
    # An explicit 7-day trip and a 30-day range cannot describe one stay.
    # Treat the range as flexible departure dates instead.
    stated = re.search(r'\b(?:длительност[ьи]\s+поездки|поездка|поехать|отдых|на)\s*(?:на\s+)?(\d{1,2})\s*(?:дн(?:ей|я|ь)|ноч(?:ей|и|ь))\b', normalized)
    if stated and int(stated.group(1)) != days:
        return None
    if days < 1 or days > 60:
        return None
    return window.earliest_departure, window.latest_departure, days


def shift_dates(text: str, request: TripRequest) -> TripRequest | None:
    """Move concrete dates or a departure window; retain all other constraints."""
    normalized = re.sub(r'\s+', ' ', text.strip().lower().replace('ё', 'е'))
    match = re.fullmatch(
        r'(?:давай\s+)?на\s+(\d{1,2}|один|два|три)\s+д(?:ень|ня|ней)\s+(позже|раньше)(?:,?\s+но\s+тот\s+же\s+бюджет)?[.!]?',
        normalized,
    )
    if not match:
        return None
    days = {'один': 1, 'два': 2, 'три': 3}.get(match.group(1))
    days = int(match.group(1)) if days is None else days
    delta = timedelta(days=days if match.group(2) == 'позже' else -days)
    data = request.model_dump(mode='python')
    if request.departure_date:
        data['departure_date'] = request.departure_date + delta
        if request.return_date:
            data['return_date'] = request.return_date + delta
    elif request.date_window:
        data['date_window'] = {
            'earliest_departure': request.date_window.earliest_departure + delta,
            'latest_departure': request.date_window.latest_departure + delta,
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
    available = ', '.join(city.name for city in cities)
    return (
        f'Направление «{destination}» сейчас отсутствует в демонстрационном каталоге. '
        f'Доступные направления: {available}. Все предложения синтетические. '
        'Могу подобрать доступную альтернативу или помочь пересмотреть направление.'
    )


def reinforce_explicit_facts(text: str, request: TripRequest) -> TripRequest:
    """Protect high-confidence facts in user message from omissions by LLM parser.

    Only applies exact supported geographical names and unambiguous Russian patterns.
    Does not infer missing data or silently relax constraints.
    """
    from app.schemas.request import Budget, Travelers
    from decimal import Decimal
    data = request.model_dump(mode='python')
    dest = explicit_destination(text)
    if dest:
        data['destination'] = dest
    if re.search(r'\b(?:из|вылет\s+из|выезд\s+из)\s+москв(?:ы|а)\b', text, re.I):
        data['origin'] = 'Москва'
    match = re.search(r'\b(?:(?:нас|ед(?:ем|ут)|поед(?:ем|ут)|взрослых)\s+)?(вдвоем|втроем|вчетвером|двое|два|две|один|одна|трое|три|четверо|четыре|[1-9])\b', text.lower().replace('ё', 'е'), re.I)
    if match:
        numbers = {'двое': 2, 'два': 2, 'две': 2, 'один': 1, 'одна': 1,
                   'трое': 3, 'три': 3, 'четверо': 4, 'четыре': 4, 'вдвоем': 2, 'втроем': 3, 'вчетвером': 4}
        number = numbers.get(match.group(1).lower())
        number = int(match.group(1)) if number is None else number
        data['travelers'] = Travelers.model_validate({**request.travelers.model_dump(mode='python'), 'adults': number})
    budget = re.search(r'\b(?:бюджет(?:\s+до)?|до|за)\s+(\d{1,3}(?:[ \u00a0]\d{3})*|\d+)\s*(тысяч(?:и|у)?|тыс\.?|руб(?:лей|ля|ль|\.)?|₽)\b', text, re.I)
    if budget:
        amount = int(re.sub(r'\s+', '', budget.group(1)))
        if budget.group(2).lower().startswith(('тыс', 'тысяч')):
            amount *= 1000
        data['budget'] = Budget(amount=Decimal(amount), currency='RUB', scope='whole_trip', is_hard_limit=True)
    return TripRequest.model_validate(data)


def destination_matches(request_destination: str | None, offer_destination: str | None) -> bool:
    """Enforce an explicitly chosen destination against the active inventory."""
    if not request_destination:
        return True
    from app.data.catalog import active_cities
    wanted = request_destination.strip().casefold()
    for city in active_cities():
        if wanted in (city.id.casefold(), city.name.casefold()):
            return city.id == (offer_destination or '').strip().casefold()
    matches = _destination_matches_text(request_destination)
    return len(matches) == 1 and matches[0][0] == (offer_destination or '').strip().casefold()


def reinforce_trip_duration(text: str, request: TripRequest) -> TripRequest:
    """Recognize an explicit duration when the LLM omits it.

    Day counts in travel requests are treated as nights in this demo, and
    should be described transparently in the UI.
    """
    normalized = text.lower().replace('ё', 'е')
    m = re.search(r'\b(?:длительност[ьи]\s+поездки|поездка|поехать|отдых|на)\s*(?:на\s+)?(\d{1,2}|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\s*(?:дн(?:ей|я|ь)|ноч(?:ей|и|ь))\b', normalized)
    if m is None:
        m = re.search(r'\b(\d{1,2}|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+(?:дн(?:ей|я|ь)|ноч(?:ей|и|ь))\b', normalized)
    if not m:
        return request
    count = {'один': 1, 'одну': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4, 'пять': 5, 'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10}.get(m.group(1))
    if count is None:
        count = int(m.group(1))
    if not 1 <= count <= 60:
        return request
    values = request.model_dump(mode='python')
    values['nights'] = count
    return TripRequest.model_validate(values)
