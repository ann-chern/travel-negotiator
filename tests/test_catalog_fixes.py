from datetime import date

from app.agent.intent_rules import explicit_destination, unsupported_destination_message
from app.data.catalog import active_cities
from app.mcp_catalog.service import CatalogService
from app.schemas.request import Travelers, TripRequest


def test_all_destinations_come_from_inventory():
    assert len(active_cities()) == 15
    assert explicit_destination("А как насчёт Пхукета?") == "Пхукет"
    assert unsupported_destination_message(TripRequest(destination="Пхукет")) is None
    assert unsupported_destination_message(TripRequest(destination="phuket")) is None
    assert "Пхукет" in unsupported_destination_message(TripRequest(destination="Япония"))


def test_main_inventory_always_contains_all_destinations():
    assert len(active_cities()) == 15
    assert unsupported_destination_message(TripRequest(destination="Пхукет")) is None


def test_cards_sorted_by_fit():
    req = TripRequest(
        origin="Москва",
        destination="Стамбул",
        departure_date=date(2027, 9, 10),
        nights=7,
        travelers=Travelers(adults=2),
    )
    result = CatalogService().search_offers(req.model_dump(mode="json"), limit=3)
    assert result.status == "ok"
    assert [o.match_score for o in result.offers] == sorted(
        [o.match_score for o in result.offers], reverse=True
    )
    assert all(
        not any("Источник климатических норм:" in x for x in o.match_caveats) for o in result.offers
    )


def test_strict_destination_filter_supports_full_catalog():
    from app.agent.intent_rules import destination_matches

    assert destination_matches("Пхукет", "phuket")
    assert not destination_matches("Пхукет", "sochi")
    assert not destination_matches("Япония", "phuket")
