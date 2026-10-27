from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.schemas.request import Budget, DateWindow, TripRequest, Travelers, Constraint
from app.services.negotiation import search_with_plan_changes


def request(**updates):
    base = dict(origin="Москва", departure_date=date(2026, 11, 10), nights=10,
                travelers=Travelers(adults=2),
                budget=Budget(amount=Decimal("48000"), currency="RUB", scope="whole_trip"))
    base.update(updates)
    return TripRequest(**base)


def test_no_relaxation_without_authorization():
    r = search_with_plan_changes(request())
    assert r.original.status == "no_match" and not r.alternatives


def test_explicit_nights_permission_preserves_hard_budget():
    r = search_with_plan_changes(request(), allow_nights=True)
    assert r.original.status == "no_match"
    assert r.alternatives
    assert all(x.kind == "nights" and x.proposed_request.budget.amount == Decimal("48000") for x in r.alternatives)
    assert all(x.best_trip.validation.status == "pass" for x in r.alternatives)


def test_hard_duration_constraint_prevents_night_proposals():
    q = request(constraints=[Constraint(field="trip.nights", operator="eq", value=10)])
    assert not search_with_plan_changes(q, allow_nights=True).alternatives


def test_date_change_requires_window_and_permission():
    q = request(date_window=DateWindow(earliest_departure=date(2026, 11, 8), latest_departure=date(2026, 11, 12)))
    assert not search_with_plan_changes(q).alternatives
    moved = search_with_plan_changes(q, allow_date_shift=True)
    assert all(x.kind == "date" for x in moved.alternatives)
    assert all(q.date_window.earliest_departure <= x.proposed_request.departure_date <= q.date_window.latest_departure for x in moved.alternatives)


def test_hard_budget_change_only_proposed_with_explicit_permission():
    q = request()
    r = search_with_plan_changes(q, allow_budget_increase=True)
    assert r.alternatives
    assert all(x.kind == "budget" for x in r.alternatives)
    assert all(x.proposed_request.budget.amount > q.budget.amount for x in r.alternatives)
    assert all(x.best_trip.validation.status == "pass" for x in r.alternatives)
    assert q.budget.amount == Decimal("48000")


def test_soft_budget_can_be_proposed_not_committed():
    q = request(budget=Budget(amount=Decimal("48000"), currency="RUB", scope="whole_trip", is_hard_limit=False))
    r = search_with_plan_changes(q, allow_budget_increase=True)
    assert r.original.status == "ready" or all(x.proposed_request.budget.amount >= q.budget.amount for x in r.alternatives)
    assert q.budget.amount == Decimal("48000")


def test_no_proposals_for_unsupported_origin():
    q = request(origin="Казань")
    result = search_with_plan_changes(q, allow_nights=True, allow_date_shift=True)
    assert result.original.status == "unsupported" and not result.alternatives


def test_invalid_limit_and_budget_cap():
    with pytest.raises(ValueError):
        search_with_plan_changes(request(), limit=0)
    with pytest.raises(ValueError):
        search_with_plan_changes(request(), max_extra_budget_fraction=Decimal("0.5"))


def test_no_mutation():
    q = request()
    snapshot = q.model_dump()
    search_with_plan_changes(q, allow_nights=True)
    assert q.model_dump() == snapshot


def test_date_shift_can_find_cheaper_season_when_window_allows():
    q = request(departure_date=date(2026, 12, 1), nights=5,
                budget=Budget(amount=Decimal("42000"), currency="RUB", scope="whole_trip"),
                date_window=DateWindow(earliest_departure=date(2026, 11, 29),
                                       latest_departure=date(2026, 12, 3)))
    r = search_with_plan_changes(q, allow_date_shift=True)
    assert r.original.status == "no_match"
    assert r.alternatives
    assert any(a.proposed_request.departure_date.month == 11 for a in r.alternatives)
    assert all(a.best_trip.validation.status == "pass" for a in r.alternatives)
