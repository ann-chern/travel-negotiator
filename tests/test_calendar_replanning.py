"""Calendar edits should replace stale periods and preserve unrelated constraints."""

from datetime import date

import pytest

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.intent_pipeline import DateKind, apply_calendar, interpret_calendar
from app.agent.llm_provider import ParsedTurn
from app.schemas.request import Budget, DateWindow, Travelers, TripRequest


@pytest.mark.parametrize(
    "old,new,target",
    [
        ("декабря", "сентябре", date(2027, 9, 1)),
        ("ноября", "январе", date(2027, 1, 1)),
        ("февраля", "июле", date(2027, 7, 1)),
        ("марта", "августе", date(2027, 8, 1)),
    ],
)
def test_replacement_targets_new_month(old, new, target):
    result = interpret_calendar(f"А если вместо {old} в {new}?", today=date(2026, 10, 28))
    assert result.kind == DateKind.FLEXIBLE_DEPARTURE
    assert result.window.earliest_departure == target


def test_explicit_year_replacement_and_state_retention():
    before = TripRequest(
        origin="Москва",
        travelers=Travelers(adults=3),
        nights=7,
        budget=Budget(amount=120000, currency="RUB"),
        date_window=DateWindow(
            earliest_departure=date(2026, 12, 1), latest_departure=date(2026, 12, 31)
        ),
    )
    change = interpret_calendar("вместо декабря в сентябре 2027", today=date(2026, 10, 28))
    after = apply_calendar(before, change)
    assert after.date_window.earliest_departure == date(2027, 9, 1)
    assert after.date_window.latest_departure == date(2027, 9, 30)
    assert after.origin == before.origin
    assert after.nights == before.nights
    assert after.travelers == before.travelers
    assert after.budget == before.budget


class StaleDateParser:
    def parse(self, text, current_state):
        # Simulates a model that emits an incorrect date: calendar edits must win.
        return ParsedTurn(intent="revise", departure_date=date(2026, 12, 1))


def test_graph_ignores_stale_llm_date_during_month_replacement():
    agent = object.__new__(GraphTravelAgent)
    agent.parser = StaleDateParser()
    req = TripRequest(
        origin="Москва",
        travelers=Travelers(adults=3),
        nights=7,
        date_window=DateWindow(
            earliest_departure=date(2026, 12, 1), latest_departure=date(2026, 12, 31)
        ),
    )
    state = TravelGraphState(
        user_message="А если вместо декабря в сентябре?", request=req, version=2
    )
    updated = TripRequest.model_validate(agent._understand(state)["request"])
    # The year is based on the runtime clock; explicit year tests above are deterministic.
    assert updated.date_window.earliest_departure.month == 9
    assert updated.date_window.earliest_departure.year >= date.today().year
    assert updated.date_window.earliest_departure != req.date_window.earliest_departure
    assert updated.travelers.adults == 3
    assert updated.nights == 7
