"""Editorial mock inventory rules, not flight timetables or actual hotel listings.

All constraints are deterministic and are intended to prevent obviously implausible
combinations. Add a new destination by declaring a profile, not branching in the agent.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HotelProfile:
    slug: str
    label: str
    stars: int
    kind: str
    meal_plan: str
    pets_allowed: bool | None
    free_cancellation: bool
    transfer_minutes: int
    room_name: str


HOTELS = (
    HotelProfile(
        "budget",
        "Городской отель",
        3,
        "city_hotel",
        "none",
        False,
        True,
        35,
        "Стандартный номер",
    ),
    HotelProfile(
        "family",
        "Семейный отель",
        4,
        "family_hotel",
        "breakfast",
        True,
        True,
        50,
        "Семейный номер",
    ),
    HotelProfile(
        "premium",
        "Премиум-отель",
        5,
        "premium_hotel",
        "breakfast",
        None,
        False,
        65,
        "Улучшенный номер",
    ),
)


@dataclass(frozen=True)
class RouteProfile:
    direct_available: bool
    direct_hours: int
    connecting_hours: int
    baggage_direct: bool = True
    baggage_connecting: bool = False
    transfer_minutes: int = 50
    transfer_multiplier: float = 1.0
    note: str = ""


# Broad *demonstration assumptions*, not verified commercial route schedules.
# Long-haul destinations require a connection in this mock inventory.
ROUTES = {
    "antalya": RouteProfile(True, 5, 8),
    "istanbul": RouteProfile(True, 4, 7),
    "sochi": RouteProfile(True, 4, 7),
    "dubai": RouteProfile(True, 6, 10),
    "phuket": RouteProfile(False, 11, 16),
    "bali": RouteProfile(False, 14, 20),
    "maldives": RouteProfile(
        False,
        10,
        15,
        transfer_minutes=100,
        transfer_multiplier=2.8,
        note="Островной трансфер (демо), лодка/самолёт не подтверждены",
    ),
    "sharm": RouteProfile(True, 6, 9),
    "hurghada": RouteProfile(True, 6, 9),
    "tbilisi": RouteProfile(True, 4, 7),
    "yerevan": RouteProfile(True, 4, 7),
    "bangkok": RouteProfile(False, 10, 15),
    "goa": RouteProfile(False, 9, 14),
    "colombo": RouteProfile(False, 9, 14),
    "kazan": RouteProfile(True, 2, 5),
}


def route_for(city_id: str) -> RouteProfile:
    return ROUTES[city_id]
