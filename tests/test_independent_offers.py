from datetime import date

from app.data.catalog import generate_candidates
from app.schemas.request import Constraint, Travelers, TripRequest
from app.services.optimizer import search_and_negotiate
from app.services.validator import validate_trip


def request(**kwargs):
    return TripRequest(
        origin="Москва",
        departure_date=date(2026, 11, 10),
        nights=5,
        travelers=Travelers(adults=2),
        **kwargs,
    )


def test_independent_fares_and_hotels_cross_product():
    items = generate_candidates(request(destination="Анталья"))
    assert len(items) == 6
    assert len({x.id for x in items}) == 6
    combinations = {(x.hotel.stars, x.outbound.stops) for x in items}
    assert combinations == {(stars, stops) for stars in (3, 4, 5) for stops in (0, 1)}
    assert all(validate_trip(x, request()).status == "pass" for x in items)


def test_fare_prices_are_independent_of_hotel():
    items = generate_candidates(request(destination="Анталья"))
    direct = [x for x in items if x.outbound.stops == 0]
    connecting = [x for x in items if x.outbound.stops == 1]
    assert len({x.outbound.id for x in direct}) == 1
    assert len({x.outbound.price.amount for x in direct}) == 1
    assert direct[0].outbound.price.amount > connecting[0].outbound.price.amount
    assert all(x.outbound.arrival_at > x.outbound.departure_at for x in items)


def test_hard_direct_flight_does_not_force_premium_hotel():
    q = request(
        destination="Анталья",
        constraints=[Constraint(field="transport.stops", operator="eq", value=0)],
    )
    r = search_and_negotiate(q, limit=10)
    assert r.status == "ready"
    assert all(x.trip.outbound.stops == 0 for x in r.ranked)
    assert any(x.trip.hotel.stars == 3 for x in r.ranked)


def test_result_limit_is_deterministic():
    q = request()
    a = generate_candidates(q, max_results=5)
    b = generate_candidates(q, max_results=5)
    assert len(a) == 5
    assert [x.id for x in a] == [x.id for x in b]
