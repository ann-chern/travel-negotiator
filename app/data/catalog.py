"""Build deterministic synthetic offers from JSON reference data."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.data.demo_pricing import (
    flight_oneway,
    hotel_nightly,
    pet_flight_surcharge,
    transfer_oneway,
)
from app.data.inventory_profiles import HOTELS, route_for
from app.data.loader import DestinationFixture, load_catalog_fixtures
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
from app.schemas.request import TripRequest
from app.schemas.trip import TripCandidate

CATALOG_VERSION = "demo-v4-json-fixtures"
_FIXTURES = load_catalog_fixtures()
DESTINATIONS: tuple[DestinationFixture, ...] = _FIXTURES.destinations

HOTEL_PROFILES = tuple(
    (
        profile.slug,
        profile.label,
        profile.stars,
        profile.meal_plan,
        profile.pets_allowed,
        profile.free_cancellation,
    )
    for profile in HOTELS
)


def active_cities() -> tuple[DestinationFixture, ...]:
    """Return the single primary catalog of 15 destinations."""
    return DESTINATIONS


def _money(value: int | Decimal) -> Money:
    return Money(amount=Decimal(value).quantize(Decimal("0.01")), currency="RUB")


def _provenance() -> Provenance:
    return Provenance(
        source_type="synthetic",
        provider="travel-negotiator-seed",
        catalog_version=CATALOG_VERSION,
        observed_at=datetime(2026, 10, 26, tzinfo=timezone.utc),
    )


def destinations() -> list[Destination]:
    """Convert destination fixtures into public catalog models."""
    return [
        Destination(
            id=destination.id,
            location=Location(
                id=destination.id,
                name=destination.name,
                country_code=destination.country,
                latitude=destination.latitude,
                longitude=destination.longitude,
                timezone_name=destination.timezone,
            ),
            kind="city",
            tags=set(destination.tags),
            transport_hub_ids=[destination.airport],
        )
        for destination in active_cities()
    ]


def hotels(destination_id: str | None = None) -> list[Hotel]:
    """Build synthetic hotels near destination centers."""
    return [
        Hotel(
            id=f"{destination.id}-{slug}",
            destination_id=destination.id,
            name=f"{destination.name} — {label} (демо)",
            stars=stars,
            latitude=destination.latitude + 0.015 * (index + 1),
            longitude=destination.longitude + 0.012 * (index + 1),
            pets_allowed=pets_allowed,
            amenities={"wifi", "breakfast"} if meal_plan != "none" else {"wifi"},
        )
        for destination in active_cities()
        if destination_id is None or destination_id == destination.id
        for index, (
            slug,
            label,
            stars,
            meal_plan,
            pets_allowed,
            free_cancellation,
        ) in enumerate(HOTEL_PROFILES)
    ]


def generate_candidates(request: TripRequest, *, max_results: int = 100) -> list[TripCandidate]:
    """Build combinations of hotels, flights, and transfers."""
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if not request.origin or request.origin.strip().casefold() not in {
        "москва",
        "moscow",
    }:
        return []
    if request.departure_date is None or request.nights is None:
        return []
    if not (date(2026, 10, 27) <= request.departure_date <= date(2027, 10, 26)):
        return []
    if not 1 <= request.nights <= 14:
        return []
    if (
        request.return_date is not None
        and request.return_date != request.departure_date + timedelta(days=request.nights)
    ):
        return []
    people = request.travelers
    if people.adults is None or people.children_ages_unknown or people.adults < 1:
        return []
    count = people.adults + len(people.children_ages)
    if not 1 <= count <= 4:
        return []
    pets_count = sum(pet.count for pet in people.pets)
    if pets_count > 1:
        return []

    occupancy = Occupancy(
        adults=people.adults,
        children_ages=list(people.children_ages),
        pets_count=pets_count,
    )
    start_date = request.departure_date
    end_date = start_date + timedelta(days=request.nights)
    result: list[TripCandidate] = []
    origin_timezone = ZoneInfo("Europe/Moscow")
    provenance = _provenance()

    for destination in active_cities():
        route = route_for(destination.id)
        if request.destination and request.destination.casefold().strip() not in {
            destination.id,
            destination.name.casefold(),
            ("турция" if destination.country == "TR" else "россия"),
            ("turkey" if destination.country == "TR" else "russia"),
        }:
            continue
        destination_timezone = ZoneInfo(destination.timezone)
        destination_hotels = {hotel.id: hotel for hotel in hotels(destination.id)}

        hotel_offers: list[tuple[str, Hotel, HotelOffer]] = []
        for (
            slug,
            label,
            stars,
            meal_plan,
            pets_allowed,
            free_cancellation,
        ) in HOTEL_PROFILES:
            hotel = destination_hotels[f"{destination.id}-{slug}"]
            nightly_rate = hotel_nightly(destination.id, slug, start_date)
            stay_price = request.nights * (nightly_rate + max(0, count - 2) * 850)
            offer = HotelOffer(
                id=(f"{hotel.id}-{start_date.isoformat()}-{request.nights}-{count}-{pets_count}"),
                hotel_id=hotel.id,
                check_in=start_date,
                check_out=end_date,
                occupancy=occupancy,
                room_name=next(profile.room_name for profile in HOTELS if profile.slug == slug),
                room_capacity=4,
                meal_plan=meal_plan,
                free_cancellation=free_cancellation,
                price=_money(stay_price),
                availability="available",
                provenance=provenance,
            )
            hotel_offers.append((slug, hotel, offer))

        flight_offers: list[tuple[str, TransportOffer, TransportOffer]] = []
        fare_profiles = [("direct", 0, route.direct_hours)] if route.direct_available else []
        fare_profiles.append(("connecting", 1, route.connecting_hours))
        for fare, stops, travel_hours in fare_profiles:
            outbound_departure = datetime.combine(
                start_date, datetime.min.time(), origin_timezone
            ).replace(hour=7)
            outbound_arrival = (outbound_departure + timedelta(hours=travel_hours)).astimezone(
                destination_timezone
            )
            arrival_day = outbound_arrival.date()
            local_checkout = arrival_day + timedelta(days=request.nights)
            inbound_departure = datetime.combine(
                local_checkout, datetime.min.time(), destination_timezone
            ).replace(hour=19)
            inbound_arrival = (inbound_departure + timedelta(hours=travel_hours)).astimezone(
                origin_timezone
            )
            fare_price = flight_oneway(destination.id, fare, start_date) * count + (
                pet_flight_surcharge(destination.id) if pets_count else 0
            )
            outbound = TransportOffer(
                id=f"{destination.id}-out-{fare}-{start_date}-{count}-{pets_count}",
                origin_hub_id="SVO",
                destination_hub_id=destination.airport,
                mode="plane",
                departure_at=outbound_departure,
                arrival_at=outbound_arrival,
                stops=stops,
                passengers=occupancy,
                baggage_included=(fare == "direct"),
                pets_allowed=True,
                price=_money(fare_price),
                availability="available",
                provenance=provenance,
            )
            inbound = TransportOffer(
                id=f"{destination.id}-back-{fare}-{end_date}-{count}-{pets_count}",
                origin_hub_id=destination.airport,
                destination_hub_id="SVO",
                mode="plane",
                departure_at=inbound_departure,
                arrival_at=inbound_arrival,
                stops=stops,
                passengers=occupancy,
                baggage_included=(fare == "direct"),
                pets_allowed=True,
                price=_money(fare_price),
                availability="available",
                provenance=provenance,
            )
            flight_offers.append((fare, outbound, inbound))

        for hotel_slug, hotel, room in hotel_offers:
            for fare, outbound, inbound in flight_offers:
                transfer_minutes = max(
                    route.transfer_minutes,
                    next(
                        profile.transfer_minutes for profile in HOTELS if profile.slug == hotel_slug
                    ),
                )
                arrival_pickup = outbound.arrival_at + timedelta(minutes=60)
                hotel_checkin = (arrival_pickup + timedelta(minutes=transfer_minutes)).date()
                hotel_checkout = hotel_checkin + timedelta(days=request.nights)
                room = room.model_copy(
                    update={
                        "check_in": hotel_checkin,
                        "check_out": hotel_checkout,
                        "id": (
                            f"{hotel.id}-{hotel_checkin.isoformat()}-"
                            f"{request.nights}-{count}-{pets_count}"
                        ),
                    }
                )
                travel_duration = inbound.arrival_at - inbound.departure_at
                inbound_departure = datetime.combine(
                    hotel_checkout,
                    datetime.min.time(),
                    destination_timezone,
                ).replace(hour=19)
                inbound = inbound.model_copy(
                    update={
                        "departure_at": inbound_departure,
                        "arrival_at": (inbound_departure + travel_duration).astimezone(
                            origin_timezone
                        ),
                        "id": (
                            f"{destination.id}-back-{fare}-{hotel_checkout}-{count}-{pets_count}"
                        ),
                    }
                )
                departure_pickup = inbound.departure_at - timedelta(hours=3)
                transfer_price = round(
                    transfer_oneway(destination.id, start_date) * route.transfer_multiplier
                ) + 350 * max(0, count - 2)
                arrival_transfer = TransferOffer(
                    id=(f"{destination.id}-tr-arr-{hotel_slug}-{fare}-{start_date}"),
                    pickup_location_id=destination.airport,
                    dropoff_location_id=hotel.id,
                    pickup_at=arrival_pickup,
                    arrival_at=arrival_pickup + timedelta(minutes=transfer_minutes),
                    passenger_capacity=4,
                    pets_allowed=True,
                    luggage_capacity=4,
                    price=_money(transfer_price),
                    availability="available",
                    provenance=provenance,
                )
                departure_transfer = TransferOffer(
                    id=(f"{destination.id}-tr-dep-{hotel_slug}-{fare}-{hotel_checkout}"),
                    pickup_location_id=hotel.id,
                    dropoff_location_id=destination.airport,
                    pickup_at=departure_pickup,
                    arrival_at=departure_pickup + timedelta(minutes=transfer_minutes),
                    passenger_capacity=4,
                    pets_allowed=True,
                    luggage_capacity=4,
                    price=_money(transfer_price),
                    availability="available",
                    provenance=provenance,
                )
                result.append(
                    TripCandidate(
                        id=(
                            f"trip-{destination.id}-{hotel_slug}-{fare}-"
                            f"{start_date}-{request.nights}-{count}-{pets_count}"
                        ),
                        destination_id=destination.id,
                        hotel=hotel,
                        hotel_offer=room,
                        outbound=outbound,
                        inbound=inbound,
                        arrival_transfer=arrival_transfer,
                        departure_transfer=departure_transfer,
                    )
                )
                if len(result) >= max_results:
                    return result
    return result
