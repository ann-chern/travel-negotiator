"""Structural plausibility tests for synthetic offers, not route verification."""

from datetime import date

from app.data.catalog import active_cities, generate_candidates
from app.data.inventory_profiles import HOTELS, ROUTES
from app.schemas.request import Travelers, TripRequest
from app.services.validator import validate_trip


def request(destination):
    return TripRequest(
        origin="Москва",
        destination=destination,
        departure_date=date(2027, 9, 12),
        nights=7,
        travelers=Travelers(adults=2),
    )


def test_all_geographic_seeds_have_route_policies(monkeypatch):
    monkeypatch.setenv("TN_EXTENDED_DEMO", "1")
    assert {c.id for c in active_cities()} == set(ROUTES)
    assert len({p.slug for p in HOTELS}) == len(HOTELS)


def test_long_haul_not_falsely_advertised_as_nonstop(monkeypatch):
    monkeypatch.setenv("TN_EXTENDED_DEMO", "1")
    for city in ("bali", "phuket", "maldives", "bangkok", "goa", "colombo"):
        items = generate_candidates(request(city))
        assert items
        assert all(t.outbound.stops > 0 and t.inbound.stops > 0 for t in items)
        assert all(validate_trip(t, request(city)).status in {"pass", "unknown"} for t in items)
        assert all(t.hotel_offer.check_in == t.arrival_transfer.arrival_at.date() for t in items)
        assert all(
            not any(i.code == "late_checkin" for i in validate_trip(t, request(city)).issues)
            for t in items
        )


def test_island_transfer_longer_and_more_costly(monkeypatch):
    monkeypatch.setenv("TN_EXTENDED_DEMO", "1")
    maldives = generate_candidates(request("maldives"))[0]
    dubai = generate_candidates(request("dubai"))[0]
    assert (
        maldives.arrival_transfer.arrival_at - maldives.arrival_transfer.pickup_at
        > dubai.arrival_transfer.arrival_at - dubai.arrival_transfer.pickup_at
    )
    assert maldives.arrival_transfer.price.amount > dubai.arrival_transfer.price.amount


def test_hotel_classes_have_distinct_room_names_and_meal_plans():
    trips = generate_candidates(request("antalya"))
    assert len({t.hotel_offer.room_name for t in trips}) == 3
    assert len({t.hotel_offer.meal_plan for t in trips}) >= 2
    assert all(t.hotel_offer.provenance.source_type.value == "synthetic" for t in trips)


def test_repeatability_and_offer_ids_across_profiles(monkeypatch):
    monkeypatch.setenv("TN_EXTENDED_DEMO", "1")
    r = request("maldives")
    first = generate_candidates(r)
    second = generate_candidates(r)
    assert [t.model_dump(mode="json") for t in first] == [t.model_dump(mode="json") for t in second]
    assert len({t.id for t in first}) == len(first)
