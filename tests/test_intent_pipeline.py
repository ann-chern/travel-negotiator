"""Cross-month, open-ended and existing-state contract tests for planning policy."""

from datetime import date

import pytest

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.intent_pipeline import (
    DateKind,
    DecisionKind,
    apply_calendar,
    decide_next,
    interpret_calendar,
)
from app.agent.llm_provider import ParsedTurn
from app.schemas.request import Travelers, TripRequest


@pytest.mark.parametrize(
    "month,now,expected_year",
    [
        ("январе", date(2026, 10, 28), 2027),
        ("сентябре", date(2026, 10, 28), 2027),
        ("декабре", date(2026, 10, 28), 2026),
        ("мае", date(2026, 10, 28), 2027),
    ],
)
def test_full_month_is_future_window(month, now, expected_year):
    intent = interpret_calendar(f"любой день в {month}", today=now)
    assert intent.kind == DateKind.FLEXIBLE_DEPARTURE
    assert intent.window.earliest_departure.year == expected_year


def test_stay_and_window_are_different_types():
    stay = interpret_calendar("Хочу в Стамбул с 10 по 15 ноября", today=date(2026, 10, 28))
    window = interpret_calendar("вылет с 10 по 15 ноября", today=date(2026, 10, 28))
    assert stay.kind == DateKind.EXACT_STAY
    assert stay.nights == 5
    assert window.kind == DateKind.FLEXIBLE_DEPARTURE


def test_calendar_change_keeps_existing_destination_party_and_budget():
    from app.schemas.request import Budget

    original = TripRequest(
        origin="Москва",
        destination="Анталья",
        travelers=Travelers(adults=3),
        nights=7,
        budget=Budget(amount="100000", currency="RUB"),
    )
    intent = interpret_calendar("в любой день декабря", today=date(2026, 10, 28))
    new = apply_calendar(original, intent)
    assert new.destination == "Анталья"
    assert new.nights == 7
    assert new.travelers.adults == 3
    assert new.budget.amount == 100000
    assert decide_next(new).kind == DecisionKind.SEARCH


def test_unsupported_destination_precedes_unneeded_questions():
    req = TripRequest(destination="Япония")
    assert decide_next(req).kind == DecisionKind.CATALOG_GAP


def test_missing_date_asks_question_but_flexible_month_does_not():
    base = TripRequest(origin="Москва", nights=7, travelers=Travelers(adults=3))
    assert decide_next(base).missing_field == "departure_date"
    updated = apply_calendar(
        base, interpret_calendar("любой день в ноябре", today=date(2026, 10, 28))
    )
    assert decide_next(updated).kind == DecisionKind.SEARCH


class FallbackParser:
    def parse(self, text, state):
        return ParsedTurn(origin="Казань", adults=2, nights=4)


def test_calendar_pipeline_merges_semantic_information():
    agent = object.__new__(GraphTravelAgent)
    agent.parser = FallbackParser()
    result = agent._understand(
        TravelGraphState(
            user_message="В любой день ноября. Вылет из Казани, едем вдвоём на четыре ночи"
        )
    )
    request = TripRequest.model_validate(result["request"])
    assert request.origin == "Казань"
    assert request.travelers.adults == 2
    assert request.nights == 4
    assert request.date_window is not None


def test_soft_beach_goal_keeps_budget_and_does_not_enforce_destination():
    from app.agent.intent_pipeline import apply_travel_goal
    from app.schemas.request import Budget

    base = TripRequest(
        origin="Москва",
        travelers=Travelers(adults=3),
        nights=7,
        date_window=interpret_calendar("в декабре", today=date(2026, 10, 28)).window,
        budget=Budget(amount="250000", currency="RUB"),
    )
    req = apply_travel_goal(base, "beach")
    assert req.destination is None
    assert req.budget.amount == 250000
    assert req.preferences[0].field == "destination.tags"
    assert req.preferences[0].desired_value == "beach"


def test_beach_goal_considers_seasonality_in_flexible_search():
    from app.agent.intent_pipeline import apply_travel_goal
    from app.mcp_catalog.service import CatalogService

    request = apply_travel_goal(
        TripRequest(
            origin="Москва",
            travelers=Travelers(adults=3),
            nights=7,
            date_window=interpret_calendar("любой день декабря", today=date(2026, 10, 28)).window,
        ),
        "beach",
    )
    result = CatalogService().search_date_window(request.model_dump(mode="json"), limit=3)
    assert result.status == "ok"
    assert all((offer.match_score or 0) > 0 for offer in result.offers)
    from app.data.catalog import active_cities

    tags = {destination.id: destination.tags for destination in active_cities()}
    assert all("beach" in tags[offer.destination_id] for offer in result.offers)
    assert all(offer.match_caveats for offer in result.offers)


class GoalParser:
    def parse(self, text, state):
        return ParsedTurn(intent="plan", travel_goal="beach")


def test_open_goal_is_saved_when_exact_facts_present():
    agent = object.__new__(GraphTravelAgent)
    agent.parser = GoalParser()
    state = TravelGraphState(
        user_message="Нас трое, хотим пляжный отдых на 7 дней. Вылет из Москвы. В декабре"
    )
    req = TripRequest.model_validate(agent._understand(state)["request"])
    assert req.travelers.adults == 3
    assert req.nights == 7
    assert req.date_window is not None
    assert req.preferences[0].desired_value == "beach"


def test_no_calendar_initial_explicit_request_can_keep_semantic_goal():
    agent = object.__new__(GraphTravelAgent)
    agent.parser = GoalParser()
    state = TravelGraphState(
        user_message="Нас трое, хотим пляжный отдых на 7 дней. Вылет из Москвы."
    )
    req = TripRequest.model_validate(agent._understand(state)["request"])
    assert req.preferences[0].desired_value == "beach"
