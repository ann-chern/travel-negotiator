from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

from app.schemas.inventory import Hotel, HotelOffer, Money, Occupancy, Provenance, TransportOffer, TransferOffer
from app.schemas.request import Budget, Travelers, TripRequest
from app.schemas.trip import TripCandidate
from app.services.validator import validate_trip

TZ = timezone.utc
BASE = datetime(2026, 11, 10, 8, tzinfo=TZ)
PROVENANCE = Provenance(source_type='synthetic', provider='test', catalog_version='1', observed_at=BASE)


def money(value, currency='RUB'):
    return Money(amount=Decimal(str(value)), currency=currency)


def make_trip():
    people = Occupancy(adults=2)
    hotel = Hotel(id='h1', destination_id='antalya', name='Demo Hotel', latitude=36.8, longitude=30.7, pets_allowed=True)
    stay = HotelOffer(id='stay', hotel_id='h1', check_in=date(2026, 11, 10), check_out=date(2026, 11, 15), occupancy=people, room_name='double', room_capacity=2, meal_plan='breakfast', price=money(40000), availability='available', provenance=PROVENANCE)
    outbound = TransportOffer(id='out', origin_hub_id='SVO', destination_hub_id='AYT', mode='plane', departure_at=BASE, arrival_at=BASE+timedelta(hours=4), stops=0, passengers=people, price=money(18000), availability='available', provenance=PROVENANCE)
    inbound = TransportOffer(id='back', origin_hub_id='AYT', destination_hub_id='SVO', mode='plane', departure_at=BASE+timedelta(days=5, hours=7), arrival_at=BASE+timedelta(days=5, hours=11), stops=0, passengers=people, price=money(17000), availability='available', provenance=PROVENANCE)
    transfer_in = TransferOffer(id='tr-in', pickup_location_id='AYT', dropoff_location_id='h1', pickup_at=BASE+timedelta(hours=5), arrival_at=BASE+timedelta(hours=6), passenger_capacity=3, price=money(1500), availability='available', provenance=PROVENANCE)
    transfer_out = TransferOffer(id='tr-out', pickup_location_id='h1', dropoff_location_id='AYT', pickup_at=BASE+timedelta(days=5,hours=3), arrival_at=BASE+timedelta(days=5,hours=4), passenger_capacity=3, price=money(1500), availability='available', provenance=PROVENANCE)
    return TripCandidate(id='t1', destination_id='antalya', hotel=hotel, hotel_offer=stay, outbound=outbound, inbound=inbound, arrival_transfer=transfer_in, departure_transfer=transfer_out)


def request():
    return TripRequest(travelers=Travelers(adults=2), nights=5, budget=Budget(amount=Decimal('80000'), currency='RUB', scope='whole_trip'))


def test_valid_trip_price():
    result = validate_trip(make_trip(), request())
    assert result.status == 'pass', result.issues
    assert result.total_price.amount == Decimal('78000')


def test_hard_budget():
    r = request().model_copy(update={'budget': Budget(amount=Decimal('70000'), currency='RUB', scope='whole_trip')})
    result = validate_trip(make_trip(), r)
    assert result.status == 'fail'
    assert 'budget_exceeded' in [i.code for i in result.issues]


def test_transfer_connection():
    trip = make_trip()
    trip.arrival_transfer.pickup_at = trip.outbound.arrival_at + timedelta(minutes=10)
    result = validate_trip(trip, request())
    assert result.status == 'fail'
    assert 'arrival_connection' in [i.code for i in result.issues]


def test_unknown_availability():
    trip = make_trip()
    trip.hotel_offer.availability = 'unknown'
    assert validate_trip(trip, request()).status == 'unknown'


def test_currency_cannot_sum():
    trip = make_trip()
    trip.hotel_offer.price = money(500, 'USD')
    result = validate_trip(trip, request())
    assert result.status == 'unknown'
    assert result.total_price is None


def test_wrong_hotel():
    trip = make_trip()
    trip.hotel_offer.hotel_id = 'other'
    assert 'hotel_destination_mismatch' in [i.code for i in validate_trip(trip, request()).issues]


def test_unconfirmed_pets():
    trip = make_trip()
    trip.hotel_offer.occupancy.pets_count = 1
    trip.outbound.passengers.pets_count = 1
    trip.inbound.passengers.pets_count = 1
    trip.outbound.pets_allowed = None
    trip.inbound.pets_allowed = True
    trip.arrival_transfer.pets_allowed = True
    trip.departure_transfer.pets_allowed = True
    req = request()
    req.travelers.pets = [dict(kind='dog', count=1)]
    # model assignment validation does not revalidate nested dict lists: validate explicitly
    req = TripRequest.model_validate(req.model_dump())
    result = validate_trip(trip, req)
    assert result.status == 'unknown'
    assert 'outbound_pets' in [i.code for i in result.issues]


def test_transfer_capacity():
    trip = make_trip()
    trip.arrival_transfer.passenger_capacity = 1
    result = validate_trip(trip, request())
    assert result.status == "fail"
    assert "arrival_transfer_capacity" in [i.code for i in result.issues]
