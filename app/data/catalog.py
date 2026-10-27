"""Small deterministic synthetic catalog: three real geographic destinations.

All hotel/transport/transfer OFFERS are fictional and not bookable. No network access.
This is the first 3-destination seed, NOT worldwide inventory or real prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.schemas.inventory import (
    Destination, Hotel, HotelOffer, Location, Money, Occupancy,
    Provenance, TransferOffer, TransportOffer,
)
from app.schemas.request import TripRequest
from app.schemas.trip import TripCandidate

CATALOG_VERSION = "demo-v1"


@dataclass(frozen=True)
class CitySeed:
    id: str
    name: str
    country: str
    lat: float
    lon: float
    timezone: str
    airport: str
    tags: frozenset[str]
    flight_hours: int
    flight_base: int  # RUB per adult each way, SYNTHETIC
    transfer_base: int  # RUB for group, one way


CITIES = (
    CitySeed("antalya", "Анталья", "TR", 36.8969, 30.7133, "Europe/Istanbul", "AYT", frozenset({"beach", "family", "warm"}), 5, 15000, 2200),
    CitySeed("istanbul", "Стамбул", "TR", 41.0082, 28.9784, "Europe/Istanbul", "IST", frozenset({"city", "culture", "food"}), 4, 11500, 2600),
    CitySeed("sochi", "Сочи", "RU", 43.5855, 39.7231, "Europe/Moscow", "AER", frozenset({"nature", "family", "sea"}), 4, 8500, 1600),
)

# Price/night for two adults; extra-person fees applied below. All examples fictional.
HOTEL_PROFILES = (
    ("budget", "Эконом", 3, 3400, "none", False, True),
    ("family", "Семейный", 4, 5600, "breakfast", True, True),
    ("premium", "Комфорт", 5, 9200, "all_inclusive", None, False),
)


def _money(value: int | Decimal) -> Money:
    return Money(amount=Decimal(value).quantize(Decimal("0.01")), currency="RUB")


def _provenance() -> Provenance:
    return Provenance(source_type="synthetic", provider="travel-negotiator-seed", catalog_version=CATALOG_VERSION, observed_at=datetime(2026, 10, 26, tzinfo=timezone.utc))


def destinations() -> list[Destination]:
    """Reference geography is real; tags are hand-authored demonstration metadata."""
    return [Destination(id=c.id, location=Location(id=c.id, name=c.name, country_code=c.country,
        latitude=c.lat, longitude=c.lon, timezone_name=c.timezone), kind="city",
        tags=set(c.tags), transport_hub_ids=[c.airport]) for c in CITIES]


def hotels(destination_id: str | None = None) -> list[Hotel]:
    """Fictional hotels, with coordinates offset from actual city centers."""
    return [Hotel(id=f"{c.id}-{slug}", destination_id=c.id, name=f"{c.name} — {label} (демо)",
        stars=stars, latitude=c.lat + 0.015 * (i + 1), longitude=c.lon + 0.012 * (i + 1),
        pets_allowed=pets, amenities={"wifi", "breakfast"} if meal != "none" else {"wifi"})
        for c in CITIES if destination_id is None or destination_id == c.id
        for i, (slug, label, stars, nightly, meal, pets, cancel) in enumerate(HOTEL_PROFILES)]


def generate_candidates(request: TripRequest, *, max_results: int = 100) -> list[TripCandidate]:
    """Assemble independent hotel and round-trip flight offers (all synthetic).

    Each city has two flight fare profiles and three hotel profiles. Transfer
    offers depend on the selected hotel and arrival/departure times.
    """
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if not request.origin or request.origin.strip().casefold() not in {"москва", "moscow"}:
        return []
    if request.departure_date is None or request.nights is None:
        return []
    if not (date(2026, 10, 27) <= request.departure_date <= date(2027, 10, 26)):
        return []
    if not 1 <= request.nights <= 14:
        return []
    if request.return_date is not None and request.return_date != request.departure_date + timedelta(days=request.nights):
        return []
    people = request.travelers
    if people.adults is None or people.children_ages_unknown or people.adults < 1:
        return []
    count = people.adults + len(people.children_ages)
    if not 1 <= count <= 4:
        return []
    pets_count = sum(p.count for p in people.pets)
    if pets_count > 1:
        return []

    occupancy = Occupancy(adults=people.adults, children_ages=list(people.children_ages), pets_count=pets_count)
    start_date = request.departure_date
    end_date = start_date + timedelta(days=request.nights)
    result: list[TripCandidate] = []
    origin_tz = ZoneInfo("Europe/Moscow")
    prov = _provenance()

    for city in CITIES:
        if request.destination and request.destination.casefold().strip() not in {
            city.id, city.name.casefold(),
            ("турция" if city.country == "TR" else "россия"),
            ("turkey" if city.country == "TR" else "russia"),
        }:
            continue
        destination_tz = ZoneInfo(city.timezone)
        city_hotels = {h.id: h for h in hotels(city.id)}
        seasonal = Decimal("1.2") if start_date.month in (6, 7, 8, 12) else Decimal("1.0")

        hotel_offers: list[tuple[str, Hotel, HotelOffer]] = []
        for slug, label, stars, nightly, meal, pets_allowed, cancellation in HOTEL_PROFILES:
            hotel = city_hotels[f"{city.id}-{slug}"]
            nightly_rate = int(Decimal(nightly) * seasonal)
            stay_price = request.nights * (nightly_rate + max(0, count - 2) * 850)
            offer = HotelOffer(
                id=f"{hotel.id}-{start_date.isoformat()}-{request.nights}-{count}-{pets_count}",
                hotel_id=hotel.id, check_in=start_date, check_out=end_date,
                occupancy=occupancy, room_name="demo family room", room_capacity=4,
                meal_plan=meal, free_cancellation=cancellation, price=_money(stay_price),
                availability="available", provenance=prov,
            )
            hotel_offers.append((slug, hotel, offer))

        # Transport is priced/generated independently from hotel star rating.
        # The connecting fare has longer journey time, but a lower price.
        flight_offers: list[tuple[str, TransportOffer, TransportOffer]] = []
        for fare, stops, extra_hours, discount in (
            ("direct", 0, 0, 0),
            ("connecting", 1, 3, 3500),
        ):
            outbound_departure = datetime.combine(start_date, datetime.min.time(), origin_tz).replace(hour=7)
            outbound_arrival = (outbound_departure + timedelta(hours=city.flight_hours + extra_hours)).astimezone(destination_tz)
            inbound_departure = datetime.combine(end_date, datetime.min.time(), destination_tz).replace(hour=19)
            inbound_arrival = (inbound_departure + timedelta(hours=city.flight_hours + extra_hours)).astimezone(origin_tz)
            fare_price = max(1000, city.flight_base - discount) * count + (city.flight_base if pets_count else 0)
            outbound = TransportOffer(
                id=f"{city.id}-out-{fare}-{start_date}-{count}-{pets_count}",
                origin_hub_id="SVO", destination_hub_id=city.airport,
                mode="plane", departure_at=outbound_departure, arrival_at=outbound_arrival,
                stops=stops, passengers=occupancy, baggage_included=(fare == "direct"),
                pets_allowed=True, price=_money(fare_price), availability="available", provenance=prov,
            )
            inbound = TransportOffer(
                id=f"{city.id}-back-{fare}-{end_date}-{count}-{pets_count}",
                origin_hub_id=city.airport, destination_hub_id="SVO",
                mode="plane", departure_at=inbound_departure, arrival_at=inbound_arrival,
                stops=stops, passengers=occupancy, baggage_included=(fare == "direct"),
                pets_allowed=True, price=_money(fare_price), availability="available", provenance=prov,
            )
            flight_offers.append((fare, outbound, inbound))

        for hotel_slug, hotel, room in hotel_offers:
            for fare, outbound, inbound in flight_offers:
                arrival_pickup = outbound.arrival_at + timedelta(minutes=60)
                departure_pickup = inbound.departure_at - timedelta(hours=3)
                transfer_price = city.transfer_base + 350 * max(0, count - 2)
                arrival_transfer = TransferOffer(
                    id=f"{city.id}-tr-arr-{hotel_slug}-{fare}-{start_date}",
                    pickup_location_id=city.airport, dropoff_location_id=hotel.id,
                    pickup_at=arrival_pickup, arrival_at=arrival_pickup + timedelta(minutes=50),
                    passenger_capacity=4, pets_allowed=True, luggage_capacity=4,
                    price=_money(transfer_price), availability="available", provenance=prov,
                )
                departure_transfer = TransferOffer(
                    id=f"{city.id}-tr-dep-{hotel_slug}-{fare}-{end_date}",
                    pickup_location_id=hotel.id, dropoff_location_id=city.airport,
                    pickup_at=departure_pickup, arrival_at=departure_pickup + timedelta(minutes=50),
                    passenger_capacity=4, pets_allowed=True, luggage_capacity=4,
                    price=_money(transfer_price), availability="available", provenance=prov,
                )
                result.append(TripCandidate(
                    id=f"trip-{city.id}-{hotel_slug}-{fare}-{start_date}-{request.nights}-{count}-{pets_count}",
                    destination_id=city.id, hotel=hotel, hotel_offer=room,
                    outbound=outbound, inbound=inbound,
                    arrival_transfer=arrival_transfer, departure_transfer=departure_transfer,
                ))
                if len(result) >= max_results:
                    return result
    return result
