from datetime import date

from app.mcp_catalog.service import CatalogService
from app.schemas.request import DateWindow, Preference, Travelers, TripRequest
from app.web.presentation import option_view


def test_matching_score_is_explained_and_bounded():
    req = TripRequest(
        origin="Москва",
        departure_date=date(2026, 12, 1),
        nights=7,
        travelers=Travelers(adults=3),
        preferences=[Preference(field="destination.tags", desired_value="beach")],
    )
    result = CatalogService().search_offers(req.model_dump(mode="json"), limit=3)
    assert result.status == "ok"
    assert len({o.destination_id for o in result.offers}) == 3
    assert all(0 <= o.match_score <= 100 and o.match_reasons for o in result.offers)
    assert all(
        any("Климатическая норма" in c or "климатических норм" in c for c in o.match_caveats)
        for o in result.offers
    )


def test_score_survives_projection():
    view = option_view(
        {
            "id": "1",
            "score": 80,
            "match_reasons": ["Есть завтрак"],
            "tradeoffs": ["Пересадки"],
            "price_rub": "100",
        }
    )
    assert view["match_score"] == 80
    assert view["match_reasons"] == ["Есть завтрак"]


def test_flexible_dates_still_search_entire_period():
    req = TripRequest(
        origin="Москва",
        date_window=DateWindow(
            earliest_departure=date(2027, 9, 1), latest_departure=date(2027, 9, 30)
        ),
        nights=7,
        travelers=Travelers(adults=3),
    )
    result = CatalogService().search_date_window(req.model_dump(mode="json"))
    assert result.status == "ok"
    assert all(
        date(2027, 9, 1) <= date.fromisoformat(o.departure_date) <= date(2027, 9, 30)
        for o in result.offers
    )
