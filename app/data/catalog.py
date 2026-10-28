"""Deterministic synthetic offer catalog with optional extended geography.

All hotel/transport/transfer OFFERS are fictional and not bookable. No network access.
The default seed is three destinations; TN_EXTENDED_DEMO=1 enables fifteen.
No commercial price or route availability is confirmed.
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

CATALOG_VERSION = "demo-v3-inventory-profiles"
from app.data.demo_pricing import hotel_nightly, flight_oneway, transfer_oneway
from app.data.inventory_profiles import HOTELS, route_for


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

# Additional geographic references; the prices and schedules remain SYNTHETIC.
# Available when TN_EXTENDED_DEMO=1. These are not harvested booking offers.
EXTENDED_CITIES = (
    CitySeed("dubai", "Дубай", "AE", 25.2048, 55.2708, "Asia/Dubai", "DXB", frozenset({"beach", "city", "warm", "family"}), 6, 24000, 4200),
    CitySeed("phuket", "Пхукет", "TH", 7.8804, 98.3923, "Asia/Bangkok", "HKT", frozenset({"beach", "warm", "nature"}), 11, 42000, 4700),
    CitySeed("bali", "Бали", "ID", -8.4095, 115.1889, "Asia/Makassar", "DPS", frozenset({"beach", "warm", "nature"}), 14, 46000, 4500),
    CitySeed("maldives", "Мале", "MV", 4.1755, 73.5093, "Indian/Maldives", "MLE", frozenset({"beach", "warm", "family"}), 10, 49000, 6300),
    CitySeed("sharm", "Шарм-эль-Шейх", "EG", 27.9158, 34.3299, "Africa/Cairo", "SSH", frozenset({"beach", "warm", "family"}), 6, 20000, 3200),
    CitySeed("hurghada", "Хургада", "EG", 27.2579, 33.8116, "Africa/Cairo", "HRG", frozenset({"beach", "warm", "family"}), 6, 19000, 2900),
    CitySeed("tbilisi", "Тбилиси", "GE", 41.7151, 44.8271, "Asia/Tbilisi", "TBS", frozenset({"city", "culture", "food"}), 4, 14500, 2200),
    CitySeed("yerevan", "Ереван", "AM", 40.1792, 44.4991, "Asia/Yerevan", "EVN", frozenset({"city", "culture", "food"}), 4, 13000, 1900),
    CitySeed("bangkok", "Бангкок", "TH", 13.7563, 100.5018, "Asia/Bangkok", "BKK", frozenset({"city", "food", "culture", "warm"}), 10, 35000, 3500),
    CitySeed("goa", "Гоа", "IN", 15.2993, 74.1240, "Asia/Kolkata", "GOI", frozenset({"beach", "warm", "nature"}), 9, 28000, 2600),
    CitySeed("colombo", "Коломбо", "LK", 6.9271, 79.8612, "Asia/Colombo", "CMB", frozenset({"beach", "city", "warm"}), 9, 31500, 3000),
    CitySeed("kazan", "Казань", "RU", 55.7879, 49.1233, "Europe/Moscow", "KZN", frozenset({"city", "culture", "food"}), 2, 7000, 1500),
)


def active_cities() -> tuple[CitySeed, ...]:
    import os
    return CITIES + EXTENDED_CITIES if os.environ.get("TN_EXTENDED_DEMO", "0") == "1" else CITIES


# Price/night for two adults; extra-person fees applied below. All examples fictional.
HOTEL_PROFILES = tuple((p.slug, p.label, p.stars, 0, p.meal_plan, p.pets_allowed, p.free_cancellation) for p in HOTELS)


def _money(value: int | Decimal) -> Money:
    return Money(amount=Decimal(value).quantize(Decimal("0.01")), currency="RUB")


def _provenance() -> Provenance:
    return Provenance(source_type="synthetic", provider="travel-negotiator-seed", catalog_version=CATALOG_VERSION, observed_at=datetime(2026, 10, 26, tzinfo=timezone.utc))


def destinations() -> list[Destination]:
    """Reference geography is real; tags are hand-authored demonstration metadata."""
    return [Destination(id=c.id, location=Location(id=c.id, name=c.name, country_code=c.country,
        latitude=c.lat, longitude=c.lon, timezone_name=c.timezone), kind="city",
        tags=set(c.tags), transport_hub_ids=[c.airport]) for c in active_cities()]


def hotels(destination_id: str | None = None) -> list[Hotel]:
    """Fictional hotels, with coordinates offset from actual city centers."""
    return [Hotel(id=f"{c.id}-{slug}", destination_id=c.id, name=f"{c.name} — {label} (демо)",
        stars=stars, latitude=c.lat + 0.015 * (i + 1), longitude=c.lon + 0.012 * (i + 1),
        pets_allowed=pets, amenities={"wifi", "breakfast"} if meal != "none" else {"wifi"})
        for c in active_cities() if destination_id is None or destination_id == c.id
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

    for city in active_cities():
        route = route_for(city.id)
        if request.destination and request.destination.casefold().strip() not in {
            city.id, city.name.casefold(),
            ("турция" if city.country == "TR" else "россия"),
            ("turkey" if city.country == "TR" else "russia"),
        }:
            continue
        destination_tz = ZoneInfo(city.timezone)
        # Long-haul departures can arrive on the following local calendar day.
        # Hotel nights must begin on actual arrival; otherwise the validator
        # correctly flags unknown late check-in and hides the whole destination.
        city_hotels = {h.id: h for h in hotels(city.id)}

        hotel_offers: list[tuple[str, Hotel, HotelOffer]] = []
        for slug, label, stars, nightly, meal, pets_allowed, cancellation in HOTEL_PROFILES:
            hotel = city_hotels[f"{city.id}-{slug}"]
            nightly_rate = hotel_nightly(city.id, slug, start_date)
            stay_price = request.nights * (nightly_rate + max(0, count - 2) * 850)
            # The arrival day depends on the flight fare. Hotel offers are
            # instantiated per fare below, after arrival has been calculated.
            offer = HotelOffer(
                id=f"{hotel.id}-{start_date.isoformat()}-{request.nights}-{count}-{pets_count}",
                hotel_id=hotel.id, check_in=start_date, check_out=end_date,
                occupancy=occupancy, room_name=next(p.room_name for p in HOTELS if p.slug == slug), room_capacity=4,
                meal_plan=meal, free_cancellation=cancellation, price=_money(stay_price),
                availability="available", provenance=prov,
            )
            hotel_offers.append((slug, hotel, offer))

        # Transport is priced/generated independently from hotel star rating.
        # The connecting fare has longer journey time, but a lower price.
        flight_offers: list[tuple[str, TransportOffer, TransportOffer]] = []
        fare_profiles = ([("direct", 0, route.direct_hours)] if route.direct_available else [])
        fare_profiles.append(("connecting", 1, route.connecting_hours))
        for fare, stops, travel_hours in fare_profiles:
            outbound_departure = datetime.combine(start_date, datetime.min.time(), origin_tz).replace(hour=7)
            outbound_arrival = (outbound_departure + timedelta(hours=travel_hours)).astimezone(destination_tz)
            arrival_day = outbound_arrival.date()
            local_checkout = arrival_day + timedelta(days=request.nights)
            inbound_departure = datetime.combine(local_checkout, datetime.min.time(), destination_tz).replace(hour=19)
            inbound_arrival = (inbound_departure + timedelta(hours=travel_hours)).astimezone(origin_tz)
            fare_price = flight_oneway(city.id, fare, start_date) * count + (city.flight_base if pets_count else 0)
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
                transfer_minutes = max(route.transfer_minutes, next(p.transfer_minutes for p in HOTELS if p.slug == hotel_slug))
                arrival_pickup = outbound.arrival_at + timedelta(minutes=60)
                hotel_checkin = (arrival_pickup + timedelta(minutes=transfer_minutes)).date()
                hotel_checkout = hotel_checkin + timedelta(days=request.nights)
                room = room.model_copy(update={
                    "check_in": hotel_checkin, "check_out": hotel_checkout,
                    "id": f"{hotel.id}-{hotel_checkin.isoformat()}-{request.nights}-{count}-{pets_count}",
                })
                travel_duration = inbound.arrival_at - inbound.departure_at
                inbound_departure = datetime.combine(hotel_checkout, datetime.min.time(), destination_tz).replace(hour=19)
                inbound = inbound.model_copy(update={
                    "departure_at": inbound_departure,
                    "arrival_at": (inbound_departure + travel_duration).astimezone(origin_tz),
                    "id": f"{city.id}-back-{fare}-{hotel_checkout}-{count}-{pets_count}",
                })
                arrival_pickup = outbound.arrival_at + timedelta(minutes=60)
                departure_pickup = inbound.departure_at - timedelta(hours=3)
                transfer_price = round(transfer_oneway(city.id, start_date) * route.transfer_multiplier) + 350 * max(0, count - 2)
                arrival_transfer = TransferOffer(
                    id=f"{city.id}-tr-arr-{hotel_slug}-{fare}-{start_date}",
                    pickup_location_id=city.airport, dropoff_location_id=hotel.id,
                    pickup_at=arrival_pickup, arrival_at=arrival_pickup + timedelta(minutes=transfer_minutes),
                    passenger_capacity=4, pets_allowed=True, luggage_capacity=4,
                    price=_money(transfer_price), availability="available", provenance=prov,
                )
                departure_transfer = TransferOffer(
                    id=f"{city.id}-tr-dep-{hotel_slug}-{fare}-{hotel_checkout}",
                    pickup_location_id=hotel.id, dropoff_location_id=city.airport,
                    pickup_at=departure_pickup, arrival_at=departure_pickup + timedelta(minutes=transfer_minutes),
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
