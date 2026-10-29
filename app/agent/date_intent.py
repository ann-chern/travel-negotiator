"""Calendar window recognition for flexible departures.

A month with no year refers to its next occurrence, not necessarily this year.
A range of possible departures is preserved as a range (never converted into a
single date by the language model). No supplier or catalog assumptions live here.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from app.constants.agent import MONTH_PATTERN, MONTH_STEMS
from app.schemas.request import DateWindow


def recognize_window(text: str, today: date | None = None) -> DateWindow | None:
    """Recognize a named month or an explicit within-month departure range.

    Without a year, use the next occurrence of that month (including the
    current month if it has remaining days). Explicit past years are not
    silently shifted. Never infer a particular departure day.
    """
    now = today or date.today()
    normalized = text.lower().replace("ё", "е")
    # In a replacement expression the month after "вместо" is the *old*
    # value, not the newly requested one. Select the last mentioned month
    # only for explicit replacement language; for ordinary text retain the
    # first month to avoid accidentally changing unrelated date mentions.
    months = list(MONTH_PATTERN.finditer(normalized))
    if not months:
        return None
    replacement = re.search(r"\bвместо\b", normalized)
    if replacement and len(months) > 1:
        targets = [m for m in months if m.start() > replacement.end()]
        match_month = targets[-1] if len(targets) > 1 else (targets[0] if targets else months[-1])
    else:
        match_month = months[0]
    month_text = match_month.group(1)
    if month_text.startswith(("май", "мая", "мае")):
        month = 5
    else:
        month = next(
            (value for stem, value in MONTH_STEMS.items() if month_text.startswith(stem)),
            None,
        )
    if month is None:
        return None
    # Interpret the year attached to the selected date phrase, not a year
    # elsewhere in the utterance describing the old date.
    suffix = normalized[match_month.end() :]
    year_match = re.search(r"^\s*(20\d{2})\b", suffix)
    if year_match is None and len(months) == 1:
        year_match = re.search(r"\b(20\d{2})\b", normalized)
    explicit_year = year_match is not None
    year = int(year_match.group(1)) if explicit_year else now.year
    if not explicit_year and month < now.month:
        year += 1
    last = calendar.monthrange(year, month)[1]
    # Only use a numeric range associated with the selected month.
    # Ranges elsewhere may describe the date being replaced.
    clause = normalized[max(0, match_month.start() - 32) : match_month.end()]
    match_range = re.search(
        r"\b(?:с\s+)?(\d{1,2})\s*(?:по|[-–—])\s*(\d{1,2})\s+" + re.escape(month_text) + r"$",
        clause,
    )
    if match_range:
        start, end = map(int, match_range.groups())
    elif (
        (len(months) > 1 and replacement is not None)
        or (
            re.search(r"\b(?:люб(?:ой|ая|ые|ую)|весь|целый)\b", normalized)
            or re.search(
                r"\b(?:в|на протяжении|в течение)\s+" + re.escape(month_text) + r"\b",
                normalized,
            )
        )
        and "половин" not in normalized
    ):
        start, end = 1, last
    elif "второй половин" in normalized or "во второй половин" in normalized:
        start, end = 16, last
    elif "первой половин" in normalized or "в первой половин" in normalized:
        start, end = 1, 15
    else:
        return None
    if start < 1 or end > last or start > end:
        return None
    if not explicit_year and year == now.year:
        if end < now.day and month == now.month:
            year += 1
        elif month == now.month:
            start = max(start, now.day)
    return DateWindow(
        earliest_departure=date(year, month, start),
        latest_departure=date(year, month, end),
    )
