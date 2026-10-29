"""Constants for agent rules and state."""

from __future__ import annotations

import re

DEFAULT_AGENT_FLAGS = {
    "allow_nights": False,
    "allow_date_shift": False,
    "allow_budget_increase": False,
}

TRAVEL_GOAL_TAGS = {
    "beach": "beach",
    "culture": "culture",
    "nature": "nature",
    "city": "city",
    "family": "family",
    "relax": "family",
}

MONTH_STEMS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
MONTH_PATTERN = re.compile(
    r"\b(январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йяе]|июн[ьяе]|"
    r"июл[ьяе]|август[ае]?|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе])\b"
)

DESTINATION_ALIASES = {
    "istanbul": (re.compile(r"\bстамбул(?:е|ь|а|у|ом)?\b", re.IGNORECASE),),
    "antalya": (re.compile(r"\bанталь(?:я|ю|е|и|ей)\b", re.IGNORECASE),),
    "sochi": (re.compile(r"\bсочи\b", re.IGNORECASE),),
    "phuket": (re.compile(r"\bпхукет(?:е|а|у|ом)?\b", re.IGNORECASE),),
    "japan": (re.compile(r"\bяпони(?:я|ю|и|ей)\b", re.IGNORECASE),),
}

NUMBER_WORDS = {
    "один": 1,
    "одна": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "двое": 2,
    "вдвоем": 2,
    "три": 3,
    "трое": 3,
    "втроем": 3,
    "четыре": 4,
    "четверо": 4,
    "вчетвером": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}

STREAM_STAGE_LABELS = {
    "understand": "Проверяю требования…",
    "route": "Определяю следующий шаг…",
    "clarify": "Готовлю уточнение…",
    "search": "Проверяю предложения…",
    "recommend": "Формирую рекомендацию…",
    "negotiate": "Проверяю альтернативы…",
    "cart_review": "Проверяю корзину…",
    "booking_confirmation": "Подтверждаю демо-бронирование…",
}

STOP_RELAXATION_PHRASES = frozenset(
    {
        "пересадку теперь можно, остальное не меняй",
        "пересадки теперь допустимы",
        "пересадки теперь можно",
        "теперь можно с пересадками",
    }
)
MEAL_RELAXATION_PHRASES = frozenset(
    {
        "и питание теперь необязательно",
        "питание теперь необязательно",
        "питание больше не важно",
        "питание больше не обязательно",
    }
)
CANCEL_BOOKING_PHRASES = frozenset({"отмена", "отменить", "не бронировать", "отказываюсь"})
DECLINE_COMPROMISE_PHRASES = frozenset({"отклоняю компромиссы", "не принимаю компромиссы"})
