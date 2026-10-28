from datetime import date

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.intent_rules import (
    shift_dates,
    trip_interval,
)
from app.agent.llm_provider import ParsedTurn
from app.schemas.request import DateWindow, Travelers, TripRequest


class MissingFieldsParser:
    def parse(self, message, current_state):
        return ParsedTurn(
            origin="Москва" if "москв" in message.lower() else None,
            adults=2 if "двое" in message.lower() else None,
            budget_rub=100000 if "100 тысяч" in message.lower() else None,
        )


def agent_without_graph():
    agent = object.__new__(GraphTravelAgent)
    agent.parser = MissingFieldsParser()
    return agent


def test_trip_interval_is_five_nights():
    stay = trip_interval(
        "Хочу в Стамбул с 10 по 15 ноября, нас двое. Бюджет 100 тысяч.",
        today=date(2026, 10, 28),
    )
    assert stay == (date(2026, 11, 10), date(2026, 11, 15), 5)
    assert trip_interval("Вылет с 5 по 7 ноября", today=date(2026, 10, 28)) is None


def test_trip_stay_commits_dates_and_destination_even_if_parser_misses_them():
    agent = agent_without_graph()
    result = agent._understand(
        TravelGraphState(
            user_message="Хочу в Стамбул с 10 по 15 ноября, нас двое. Бюджет 100 тысяч."
        )
    )
    request = TripRequest.model_validate(result["request"])
    assert request.destination == "Стамбул"
    assert request.departure_date == date(2026, 11, 10)
    assert request.return_date == date(2026, 11, 15)
    assert request.nights == 5
    assert request.travelers.adults == 2
    assert request.budget.amount == 100000


def test_shift_preserves_budget_people_destination():
    original = TripRequest(
        destination="Стамбул",
        departure_date=date(2026, 11, 10),
        return_date=date(2026, 11, 15),
        nights=5,
        travelers=Travelers(adults=2),
    )
    updated = shift_dates("Давай на два дня позже, но тот же бюджет.", original)
    assert updated.departure_date == date(2026, 11, 12)
    assert updated.return_date == date(2026, 11, 17)
    assert updated.destination == original.destination
    assert updated.travelers == original.travelers


def test_shift_window_without_choosing_arbitrary_day():
    original = TripRequest(
        date_window=DateWindow(
            earliest_departure=date(2026, 11, 5), latest_departure=date(2026, 11, 7)
        )
    )
    updated = shift_dates("на два дня позже", original)
    assert updated.date_window.earliest_departure == date(2026, 11, 7)
    assert updated.date_window.latest_departure == date(2026, 11, 9)
    assert updated.departure_date is None


def test_unsupported_destination_precedes_clarifications():
    agent = agent_without_graph()
    route = agent._route(TravelGraphState(request=TripRequest(destination="Япония")))
    assert route["stage"] == "unsupported"
    assert "Стамбул" in route["response"]
    assert agent._next(TravelGraphState(stage="unsupported")) == "end"


def test_japan_extracted_when_parser_omits_it():
    agent = agent_without_graph()
    result = agent._understand(
        TravelGraphState(user_message="Хочу в Японию на две недели за 30 тысяч рублей")
    )
    assert TripRequest.model_validate(result["request"]).destination == "Япония"
