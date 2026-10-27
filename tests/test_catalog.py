from datetime import date
from decimal import Decimal

from app.data.catalog import destinations, hotels, generate_candidates
from app.schemas.request import TripRequest, Travelers, Budget, Pet
from app.services.validator import validate_trip


def req(**kwargs):
    return TripRequest(origin="Москва", departure_date=date(2026, 11, 10), nights=5,
        travelers=Travelers(adults=2), **kwargs)


def test_reference_catalog_three_cities_nine_hotels():
    assert len(destinations()) == 3
    assert len(hotels()) == 9
    assert all(h.id.startswith(h.destination_id) for h in hotels())


def test_repeatability_across_calls():
    a = [t.model_dump(mode="json") for t in generate_candidates(req())]
    b = [t.model_dump(mode="json") for t in generate_candidates(req())]
    assert a == b
    assert len(a) == 18


def test_all_simple_trips_validate():
    request = req()
    trips = generate_candidates(request)
    assert trips
    assert all(validate_trip(t, request).status == "pass" for t in trips)


def test_country_filter_and_prices_are_different():
    ts = generate_candidates(req(destination="Турция"))
    assert len(ts) == 12
    assert {t.destination_id for t in ts} == {"antalya", "istanbul"}
    prices = {validate_trip(t, req()).total_price.amount for t in ts}
    assert len(prices) > 3


def test_change_nights_recalculates_stay():
    first = generate_candidates(req())[0]
    second_req = req().model_copy(update={"nights": 6})
    second = generate_candidates(second_req)[0]
    assert first.hotel.id == second.hotel.id
    assert first.hotel_offer.price.amount < second.hotel_offer.price.amount
    assert first.hotel_offer.check_out != second.hotel_offer.check_out
    assert validate_trip(second, second_req).status == "pass"


def test_pet_policy_is_not_faked():
    request = req().model_copy(update={"travelers": Travelers(adults=2, pets=[Pet(kind="dog")])})
    trips = generate_candidates(request)
    assert any(validate_trip(t, request).status == "pass" for t in trips)
    assert any(validate_trip(t, request).status in {"fail", "unknown"} for t in trips)


def test_outside_supported_coverage_is_empty():
    assert not generate_candidates(req().model_copy(update={"origin":"Берлин"}))
    assert not generate_candidates(req().model_copy(update={"destination":"Токио"}))
    assert not generate_candidates(req().model_copy(update={"departure_date":date(2030, 1, 1)}))


def test_budget_changes_validation_not_raw_catalog():
    cheap = req(budget=Budget(amount=Decimal("10000"), currency="RUB", scope="whole_trip"))
    trips = generate_candidates(cheap)
    assert trips
    assert all(validate_trip(t, cheap).status == "fail" for t in trips)
