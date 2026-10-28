from datetime import date

from app.mcp_catalog.contracts import OfferSummary
from app.mcp_catalog.service import _portfolio
from app.schemas.request import Travelers, TripRequest


def offer(name, price, *, stars=3, meal="none", stops=1):
    return OfferSummary(
        offer_id=name,
        destination_id="phuket",
        hotel_name="Hotel 3*",
        departure_date="2026-12-17",
        nights=7,
        price_rub=str(price),
        hotel_stars=stars,
        meal_plan=meal,
        outbound_stops=stops,
        inbound_stops=stops,
        availability="available",
        provenance="synthetic",
        observed_at="2026-10-01T00:00:00Z",
        is_bookable=False,
    )


def request():
    return TripRequest(
        origin="Москва",
        destination="Пхукет",
        departure_date=date(2026, 12, 17),
        nights=7,
        travelers=Travelers(adults=3),
    )


def test_same_hotel_and_flight_cannot_be_called_more_comfortable():
    selected = _portfolio(request(), [offer("a", 436240), offer("b", 439597)], 2)
    assert len(selected) == 2
    assert not any("Повышенный комфорт" in o.match_reasons for o in selected)
    assert not any("Меньше пересадок" in o.match_reasons for o in selected)


def test_comfort_label_requires_actual_improvement():
    selected = _portfolio(
        request(),
        [offer("basic", 1000), offer("premium", 3000, stars=5, meal="breakfast")],
        2,
    )
    premium = next(o for o in selected if o.offer_id == "premium")
    assert "Повышенный комфорт" in premium.match_reasons


def test_direct_label_requires_fewer_stops():
    selected = _portfolio(request(), [offer("basic", 1000), offer("direct", 2000, stops=0)], 2)
    direct = next(o for o in selected if o.offer_id == "direct")
    assert "Меньше пересадок" in direct.match_reasons
