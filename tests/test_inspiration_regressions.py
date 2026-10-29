from datetime import date

from app.agent.date_intent import recognize_window
from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.mcp_catalog.service import CatalogService
from app.schemas.request import Travelers, TripRequest


class OfflineParser:
    def parse(self, *_):
        raise AssertionError("Unnecessary LLM call for explicit request")


def agent():
    a = object.__new__(GraphTravelAgent)
    a.parser = OfflineParser()
    return a


def request_from(message):
    return TripRequest.model_validate(
        agent()._understand(TravelGraphState(user_message=message))["request"]
    )


def test_month_flexible_without_reasking_exact_date():
    req = request_from(
        "Нас трое, длительность поездки 7 дней, вылет из Москвы. Любой день в ноябре."
    )
    assert req.origin == "Москва"
    assert req.travelers.adults == 3
    assert req.nights == 7
    assert req.date_window.earliest_departure == date(2026, 11, 1)
    assert req.date_window.latest_departure == date(2026, 11, 30)
    assert agent()._route(TravelGraphState(request=req))["stage"] == "searching"


def test_explicit_duration_with_month_range_is_departure_window():
    req = request_from(
        "Нас трое, хотим на море. Длительность поездки 7 дней. Вылет из Москвы. С 1 по 30 ноября."
    )
    assert req.nights == 7
    assert req.departure_date is None
    assert req.date_window.latest_departure == date(2026, 11, 30)


def test_inspiration_across_destinations_from_window():
    req = TripRequest(
        origin="Москва",
        nights=7,
        travelers=Travelers(adults=3),
        date_window=recognize_window("в ноябре", today=date(2026, 10, 28)),
    )
    result = CatalogService().search_date_window(req.model_dump(mode="json"), limit=3)
    assert result.status == "ok"
    assert len({offer.destination_id for offer in result.offers}) == 3
    assert [offer.match_score for offer in result.offers] == sorted(
        [offer.match_score for offer in result.offers], reverse=True
    )
    assert all(offer.departure_date.startswith("2026-11-") for offer in result.offers)


def test_explicit_destination_does_not_add_unrequested_cities():
    req = TripRequest(
        origin="Москва",
        destination="Стамбул",
        nights=5,
        travelers=Travelers(adults=2),
        departure_date=date(2026, 11, 10),
    )
    result = CatalogService().search_offers(req.model_dump(mode="json"), limit=3)
    assert result.status == "ok"
    assert {offer.destination_id for offer in result.offers} == {"istanbul"}


def test_initial_message_with_no_date_asks_date_only():
    req = request_from("Нас трое, мы хотим на море. Длительность поездки 7 дней. Вылет из Москвы.")
    assert agent()._route(TravelGraphState(request=req))["missing"] == [
        "дату или период отправления"
    ]
