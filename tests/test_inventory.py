from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.schemas.inventory import (
    Destination,
    Hotel,
    HotelOffer,
    Location,
    Money,
    Occupancy,
    Provenance,
    TransferOffer,
    TransportOffer,
)

UTC = timezone.utc


def provenance():
    return Provenance(
        source_type="synthetic",
        provider="seed-generator",
        catalog_version="v1",
        observed_at=datetime(2026, 10, 26, tzinfo=UTC),
    )


def test_location_real_coordinates_and_timezone():
    city = Location(
        id="antalya",
        name="Анталья",
        country_code="tr",
        latitude=36.9,
        longitude=30.7,
        timezone_name="Europe/Istanbul",
    )
    assert city.country_code == "TR"
    assert Destination(id="antalya", location=city, tags={"beach"}).tags == {"beach"}
    with pytest.raises(ValidationError):
        Location(
            id="x",
            name="X",
            country_code="TR",
            latitude=100,
            longitude=30,
            timezone_name="Europe/Istanbul",
        )
    with pytest.raises(ValidationError):
        Location(
            id="x",
            name="X",
            country_code="TR",
            latitude=30,
            longitude=30,
            timezone_name="Not/A_Timezone",
        )


def test_money_uses_decimal_and_currency_normalization():
    amount = Money(amount="1234.50", currency="rub")
    assert amount.amount == Decimal("1234.50") and amount.currency == "RUB"
    with pytest.raises(ValidationError):
        Money(amount="-2", currency="RUB")


def test_unknown_pet_rule_does_not_mean_allowed():
    hotel = Hotel(id="h1", destination_id="antalya", name="Demo", latitude=36.9, longitude=30.7)
    assert hotel.pets_allowed is None


def test_hotel_offer_validates_stay_and_capacity():
    base = dict(
        id="ho1",
        hotel_id="h1",
        check_in=date(2026, 11, 1),
        check_out=date(2026, 11, 6),
        occupancy=Occupancy(adults=2, children_ages=[5]),
        room_name="family",
        room_capacity=3,
        meal_plan="breakfast",
        price=Money(amount=25000, currency="RUB"),
        availability="available",
        provenance=provenance(),
    )
    assert HotelOffer(**base).check_out == date(2026, 11, 6)
    with pytest.raises(ValidationError):
        HotelOffer(**(base | {"room_capacity": 2}))
    with pytest.raises(ValidationError):
        HotelOffer(**(base | {"check_out": date(2026, 11, 1)}))


def test_transport_compares_absolute_time_not_local_clock():
    dep = datetime(2026, 11, 1, 23, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    arrival = datetime(2026, 11, 2, 2, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
    kwargs = dict(
        id="f1",
        origin_hub_id="svo",
        destination_hub_id="ayt",
        mode="plane",
        departure_at=dep,
        arrival_at=arrival,
        stops=0,
        passengers=Occupancy(adults=2),
        price=Money(amount=20000, currency="RUB"),
        availability="available",
        provenance=provenance(),
    )
    assert TransportOffer(**kwargs).arrival_at > TransportOffer(**kwargs).departure_at
    with pytest.raises(ValidationError):
        TransportOffer(**(kwargs | {"arrival_at": dep - timedelta(hours=1)}))
    with pytest.raises(ValidationError):
        TransportOffer(**(kwargs | {"departure_at": dep.replace(tzinfo=None)}))


def test_transfer_checks_timestamps():
    pickup = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
    base = dict(
        id="tx1",
        pickup_location_id="ayt",
        dropoff_location_id="h1",
        pickup_at=pickup,
        arrival_at=pickup + timedelta(hours=1),
        passenger_capacity=3,
        price=Money(amount=3000, currency="RUB"),
        availability="available",
        provenance=provenance(),
    )
    assert TransferOffer(**base).passenger_capacity == 3
    with pytest.raises(ValidationError):
        TransferOffer(**(base | {"arrival_at": pickup}))


def test_missing_availability_is_not_bookable():
    base = dict(
        id="tx1",
        pickup_location_id="ayt",
        dropoff_location_id="h1",
        pickup_at=datetime(2026, 11, 2, tzinfo=UTC),
        arrival_at=datetime(2026, 11, 2, 1, tzinfo=UTC),
        passenger_capacity=3,
        price=Money(amount=3000, currency="RUB"),
        availability="unknown",
        provenance=provenance(),
    )
    assert TransferOffer(**base).availability == "unknown"


def test_no_zero_human_occupancy():
    with pytest.raises(ValidationError):
        Occupancy(adults=0)
