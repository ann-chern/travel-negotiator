"""UI projection of GraphReply, independent of Streamlit and providers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


MEALS = {"none": "Без питания", "breakfast": "Завтрак", "half_board": "Полупансион", "all_inclusive": "Всё включено"}
STAGES = {
    "ready": "Найдены варианты", "needs_input": "Нужно уточнение",
    "negotiating": "Предложены компромиссы", "awaiting_confirmation": "Ожидается подтверждение",
    "booked_demo": "Демо-бронирование создано", "no_match": "Нет подходящих вариантов",
    "unsupported": "Нет покрытия каталога", "error": "Ошибка обработки",
}


def money_rub(value: Any) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        return f"{amount:,.0f}".replace(",", "\u00a0") + " ₽"
    except (InvalidOperation, TypeError, ValueError):
        return "Цена неизвестна"


def option_view(row: dict[str, Any]) -> dict[str, str]:
    stops_out, stops_back = row.get("outbound_stops"), row.get("inbound_stops")
    if stops_out == 0 and stops_back == 0:
        flight = "Прямой перелёт туда и обратно"
    elif isinstance(stops_out, int) and isinstance(stops_back, int):
        flight = f"Пересадки: туда — {stops_out}, обратно — {stops_back}"
    else:
        flight = "Маршрут не указан"
    meal_code = row.get("meal_plan")
    meal = MEALS.get(meal_code, "Питание не указано")
    stars = row.get("hotel_stars")
    pets = row.get("hotel_pets_allowed")
    pets_text = "С животными можно" if pets is True else ("Животные не допускаются" if pets is False else "Животные: нет данных")
    return {
        "hotel": str(row.get("hotel") or row.get("hotel_name") or "Предложение"),
        "destination": str(row.get("destination") or row.get("destination_id") or "Направление не указано"),
        "price": money_rub(row.get("price_rub")),
        "date": str(row.get("departure_date") or "Дата не указана"),
        "duration": f"{row['nights']} ночей" if isinstance(row.get("nights"), int) else "Продолжительность не указана",
        "stars": f"{stars}★" if isinstance(stars, int) else "Категория не указана",
        "meal": meal,
        "flight": flight,
        "pets": pets_text,
        "disclaimer": "Демо-предложение · цена и доступность синтетические · реальная бронь недоступна",
    }


def commands_for_status(status: str, options_count: int, compromises_count: int) -> list[tuple[str, str]]:
    if status == "ready":
        return [(f"Выбрать вариант {idx + 1}", f"Беру {['первый','второй','третий'][idx]} вариант") for idx in range(min(options_count, 3))]
    if status == "negotiating":
        return [(f"Принять компромисс {idx + 1}", f"принимаю компромисс {idx + 1}") for idx in range(min(compromises_count, 3))] + [("Отклонить компромиссы", "отклоняю компромиссы")]
    if status == "awaiting_confirmation":
        return [("Подтвердить ДЕМО-бронирование", "подтверждаю демо-бронирование"), ("Отменить", "отмена")]
    return []
