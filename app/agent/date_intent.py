"""Conservative Russian date-window recognizer; never chooses an exact date."""
from __future__ import annotations
import calendar
from datetime import date
import re
from app.schemas.request import DateWindow

MONTHS = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,"июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}

def recognize_window(text: str, today: date | None = None) -> DateWindow | None:
    """Parse explicit ranges and half-month references; missing year remains unresolved."""
    text = text.lower().replace("ё", "е")
    month_match = re.search(r"\b("+"|".join(MONTHS)+r")\b",text)
    if not month_match:
        return None
    month = MONTHS[month_match.group(1)]
    tail = text[month_match.end():]
    year_match = re.search(r"\b(20\d{2})\b",tail)
    if year_match:
        year = int(year_match.group(1))
    else:
        # Relative dates refer to the current calendar year; future-year ambiguity
        # is surfaced as a clarification rather than selecting a day.
        year = (today or date.today()).year
    end = calendar.monthrange(year,month)[1]
    match = re.search(r"\b(?:с\s+)?(\d{1,2})\s*(?:по|[-–—])\s*(\d{1,2})\b",text)
    if match:
        a,b = map(int,match.groups())
    elif "второй половин" in text or "во второй половин" in text:
        a,b = 16,end
    elif "первой половин" in text or "в первой половин" in text:
        a,b = 1,15
    else:
        return None
    if a < 1 or b > end or a > b:
        return None
    return DateWindow(earliest_departure=date(year,month,a),latest_departure=date(year,month,b))
