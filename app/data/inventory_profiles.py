"""Доступ к валидированным профилям отелей и маршрутов из JSON-каталога."""

from app.data.loader import HotelProfile, RouteProfile, load_catalog_fixtures

_FIXTURES = load_catalog_fixtures()
HOTELS: tuple[HotelProfile, ...] = _FIXTURES.hotels
ROUTES: dict[str, RouteProfile] = _FIXTURES.routes


def route_for(destination_id: str) -> RouteProfile:
    return ROUTES[destination_id]
