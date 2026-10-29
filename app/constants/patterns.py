"""Скомпилированные регулярные выражения для правил агента."""

import re

WHITESPACE_PATTERN = re.compile(r"\s+")
TRAILING_PUNCTUATION_PATTERN = re.compile(r"[.!?]+$")
FLIGHT_DEPARTURE_WINDOW_PATTERN = re.compile(
    r"\b(?:вылет|вылететь|отправлени[ея]|выехать|вые(зд|зжа))\s+"
    r"(?:примерно\s+)?с\s+\d"
)
TRIP_CONTEXT_PATTERN = re.compile(r"\b(?:хочу|поездк[аиу]|отдых|едем|поехать|путешестви[ея])\b")
DATE_RANGE_PATTERN = re.compile(r"\b(?:с\s+)?\d{1,2}\s*(?:по|[-–—])\s*\d{1,2}\b")
STATED_DURATION_PATTERN = re.compile(
    r"\b(?:длительност[ьи]\s+поездки|поездка|поехать|отдых|на)\s*"
    r"(?:на\s+)?(\d{1,2})\s*(?:дн(?:ей|я|ь)|ноч(?:ей|и|ь))\b"
)
DATE_SHIFT_PATTERN = re.compile(
    r"(?:давай\s+)?на\s+(\d{1,2}|один|два|три)\s+д(?:ень|ня|ней)\s+"
    r"(позже|раньше)(?:,?\s+но\s+тот\s+же\s+бюджет)?[.!]?"
)
MOSCOW_ORIGIN_PATTERN = re.compile(
    r"\b(?:из|вылет\s+из|выезд\s+из)\s+москв(?:ы|а)\b", re.IGNORECASE
)
ADULT_COUNT_PATTERN = re.compile(
    r"\b(?:(?:нас|ед(?:ем|ут)|поед(?:ем|ут)|взрослых)\s+)?"
    r"(вдвоем|втроем|вчетвером|двое|два|две|один|одна|трое|три|"
    r"четверо|четыре|[1-9])\b",
    re.IGNORECASE,
)
BUDGET_PATTERN = re.compile(
    r"\b(?:бюджет(?:\s+до)?|до|за)\s+"
    r"(\d{1,3}(?:[ \u00a0]\d{3})*|\d+)\s*"
    r"(тысяч(?:и|у)?|тыс\.?|руб(?:лей|ля|ль|\.)?|₽)\b",
    re.IGNORECASE,
)
DURATION_CONTEXT_PATTERN = re.compile(
    r"\b(?:длительност[ьи]\s+поездки|поездка|поехать|отдых|на)\s*"
    r"(?:на\s+)?(\d{1,2}|один|одну|два|две|три|четыре|пять|шесть|"
    r"семь|восемь|девять|десять)\s*(?:дн(?:ей|я|ь)|ноч(?:ей|и|ь))\b"
)
DURATION_PATTERN = re.compile(
    r"\b(\d{1,2}|один|одну|два|две|три|четыре|пять|шесть|семь|"
    r"восемь|девять|десять)\s+(?:дн(?:ей|я|ь)|ноч(?:ей|и|ь))\b"
)
COMPROMISE_ACCEPT_PATTERN = re.compile(r"принимаю компромисс ([1-3])")
ADULT_ANSWER_PATTERN = re.compile(r"[1-9]|[12][0-9]|30")
