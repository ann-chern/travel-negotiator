from app.agent.intent_pipeline import apply_travel_goal
from app.agent.intent_rules import reinforce_explicit_facts, reinforce_trip_duration
from app.mcp_catalog.service import CatalogService
from app.schemas.request import TripRequest


def test_word_numbers_and_flexible_duration_no_clarification():
    msg = "Вдвоём на море, семь ночей в сентябре 2027 года, вылет из Москвы, бюджет 30 тысяч рублей"
    req = reinforce_trip_duration(msg, reinforce_explicit_facts(msg, TripRequest()))
    assert (req.travelers.adults, req.nights, req.origin) == (2, 7, "Москва")
    assert req.budget.amount == 30000


def test_phuket_is_searchable_during_december(monkeypatch):
    monkeypatch.setenv("TN_EXTENDED_DEMO", "1")
    req = TripRequest.model_validate(
        {
            "origin": "Москва",
            "destination": "Пхукет",
            "date_window": {
                "earliest_departure": "2026-12-01",
                "latest_departure": "2026-12-31",
            },
            "nights": 7,
            "travelers": {"adults": 3},
        }
    )
    response = CatalogService().search_date_window(req.model_dump(mode="json"), limit=3)
    assert response.status == "ok"
    assert response.offers and all(o.destination_id == "phuket" for o in response.offers)


def test_open_december_beach_search_excludes_zero_fit(monkeypatch):
    monkeypatch.setenv("TN_EXTENDED_DEMO", "1")
    req = TripRequest.model_validate(
        {
            "origin": "Москва",
            "date_window": {
                "earliest_departure": "2026-12-01",
                "latest_departure": "2026-12-31",
            },
            "nights": 7,
            "travelers": {"adults": 3},
        }
    )
    req = apply_travel_goal(req, "beach")
    response = CatalogService().search_date_window(req.model_dump(mode="json"), limit=3)
    assert response.status == "ok"
    assert all((offer.match_score or 0) > 0 for offer in response.offers)
    assert all(offer.destination_id != "istanbul" for offer in response.offers)
