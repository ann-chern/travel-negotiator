from datetime import date
from decimal import Decimal

from app.schemas.request import Budget, Constraint, Importance, Operator, Preference, Strength, Travelers, TripRequest
from app.services.optimizer import search_and_negotiate


def req(**kwargs):
    return TripRequest(origin="Москва", departure_date=date(2026, 11, 10), nights=5,
                       travelers=Travelers(adults=2), **kwargs)


def test_ranking_returns_only_valid_trips():
    result = search_and_negotiate(req(), limit=3)
    assert result.status == "ready" and len(result.ranked) == 3
    assert all(t.validation.status == "pass" for t in result.ranked)
    assert [x.score for x in result.ranked] == sorted((x.score for x in result.ranked), reverse=True)


def test_hard_budget_never_exceeded():
    result = search_and_negotiate(req(budget=Budget(amount=Decimal("10000"), currency="RUB", scope="whole_trip")))
    assert result.status == "no_match"
    assert not result.ranked and not result.compromises


def test_hard_stars_filter():
    c = Constraint(field="hotel.stars", operator="gte", value=4)
    r = search_and_negotiate(req(constraints=[c]))
    assert r.status == "ready" and all(x.trip.hotel.stars >= 4 for x in r.ranked)


def test_relaxable_hard_requirement_is_only_proposed():
    c = Constraint(field="hotel.stars", operator="gte", value=6, relaxation_allowed=True)
    r = search_and_negotiate(req(constraints=[c]))
    assert r.status == "no_match" and r.compromises
    assert not r.ranked
    assert all(x.trip.trip.hotel.stars < 6 for x in r.compromises)


def test_non_relaxable_constraint_not_overridden():
    c = Constraint(field="hotel.stars", operator="gte", value=6)
    r = search_and_negotiate(req(constraints=[c]))
    assert r.status == "no_match" and not r.compromises


def test_soft_preference_affects_ranking():
    r = search_and_negotiate(req(preferences=[Preference(field="hotel.stars", desired_value=5, importance=Importance.HIGH)]))
    assert r.status == "ready"
    assert r.ranked[0].trip.hotel.stars == 5


def test_unknown_catalog_property_does_not_pass_silently():
    c = Constraint(field="hotel.distance_to_center_km", operator="lte", value=2)
    r = search_and_negotiate(req(constraints=[c]))
    assert r.status == "unsupported"


def test_missing_fields_needs_input():
    r = search_and_negotiate(TripRequest())
    assert r.status == "needs_input"


def test_unsupported_origin_explicit():
    r = search_and_negotiate(req().model_copy(update={"origin": "Казань"}))
    assert r.status == "unsupported"


def test_country_constraint():
    c = Constraint(field="destination.country", operator="eq", value="TR")
    r = search_and_negotiate(req(constraints=[c]), limit=10)
    assert r.status == "ready"
    assert all(x.trip.destination_id in ("antalya", "istanbul") for x in r.ranked)


def test_soft_stop_preference_no_hard_filter():
    c = Constraint(field="transport.stops", operator="eq", value=0, strength=Strength.SOFT)
    r = search_and_negotiate(req(constraints=[c]), limit=9)
    assert r.status == "ready" and len(r.ranked) == 9
    assert r.ranked[0].trip.outbound.stops == 0


def test_unresolved_is_blocking_not_ignored():
    from app.schemas.request import UnresolvedRequirement
    r = search_and_negotiate(req(unresolved=[UnresolvedRequirement(text="без лестниц", reason="unsupported_by_catalog")]))
    assert r.status == "unsupported"
