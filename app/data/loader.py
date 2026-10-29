"""Загрузка и проверка статического синтетического каталога из JSON."""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MealPlan(StrEnum):
    NONE = "none"
    BREAKFAST = "breakfast"
    HALF_BOARD = "half_board"
    ALL_INCLUSIVE = "all_inclusive"


class DestinationFixture(FixtureModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    country: str = Field(min_length=2, max_length=2)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(min_length=1)
    airport: str = Field(min_length=3, max_length=3)
    tags: frozenset[str] = Field(min_length=1)

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value


class HotelProfile(FixtureModel):
    slug: str = Field(min_length=1)
    label: str = Field(min_length=1)
    stars: int = Field(ge=1, le=5)
    kind: str = Field(min_length=1)
    meal_plan: MealPlan
    pets_allowed: bool | None = Field(default=None)
    free_cancellation: bool
    transfer_minutes: int = Field(gt=0)
    room_name: str = Field(min_length=1)


class RouteProfile(FixtureModel):
    destination_id: str = Field(min_length=1)
    direct_available: bool
    direct_hours: int = Field(gt=0)
    connecting_hours: int = Field(gt=0)
    baggage_direct: bool = Field(default=True)
    baggage_connecting: bool = Field(default=False)
    transfer_minutes: int = Field(default=50, gt=0)
    transfer_multiplier: float = Field(default=1.0, gt=0)
    note: str = Field(default="")


class DestinationPriceBand(FixtureModel):
    destination_id: str = Field(min_length=1)
    hotel_nightly_3star: int = Field(gt=0)
    flight_oneway_per_person: int = Field(gt=0)
    transfer_oneway_group: int = Field(gt=0)
    pet_flight_surcharge: int = Field(ge=0)
    monthly_factors: tuple[float, ...]

    @field_validator("monthly_factors")
    @classmethod
    def twelve_positive_months(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if len(value) != 12 or any(factor <= 0 for factor in value):
            raise ValueError("monthly_factors must contain 12 positive values")
        return value


class DestinationsDocument(FixtureModel):
    destinations: tuple[DestinationFixture, ...]


class HotelsDocument(FixtureModel):
    profiles: tuple[HotelProfile, ...]


class RoutesDocument(FixtureModel):
    routes: tuple[RouteProfile, ...]


class RawDestinationPricing(FixtureModel):
    destination_id: str = Field(min_length=1)
    hotel_nightly_3star: int = Field(gt=0)
    flight_oneway_per_person: int = Field(gt=0)
    transfer_oneway_group: int = Field(gt=0)
    pet_flight_surcharge: int = Field(ge=0)
    seasonality: str = Field(min_length=1)


class PricingDocument(FixtureModel):
    monthly_factors: dict[str, tuple[float, ...]]
    hotel_levels: dict[str, Decimal]
    destinations: tuple[RawDestinationPricing, ...]

    @field_validator("monthly_factors")
    @classmethod
    def valid_monthly_profiles(
        cls, value: dict[str, tuple[float, ...]]
    ) -> dict[str, tuple[float, ...]]:
        if not value:
            raise ValueError("at least one monthly factor profile is required")
        for name, factors in value.items():
            if len(factors) != 12 or any(factor <= 0 for factor in factors):
                raise ValueError(f"monthly profile {name!r} must contain 12 positive values")
        return value

    @model_validator(mode="after")
    def known_seasonality_profiles(self):
        unknown = {
            row.seasonality
            for row in self.destinations
            if row.seasonality not in self.monthly_factors
        }
        if unknown:
            raise ValueError(f"unknown seasonality profiles: {sorted(unknown)}")
        return self


class CatalogFixtures(FixtureModel):
    destinations: tuple[DestinationFixture, ...]
    hotels: tuple[HotelProfile, ...]
    routes: dict[str, RouteProfile]
    price_bands: dict[str, DestinationPriceBand]
    hotel_levels: dict[str, Decimal]


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _unique_by_id(rows: tuple[FixtureModel, ...], field: str, document: str) -> None:
    values = [getattr(row, field) for row in rows]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field} in {document}")


@lru_cache(maxsize=1)
def load_catalog_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> CatalogFixtures:
    """Прочитать все справочники и проверить их взаимную целостность."""
    destinations = DestinationsDocument.model_validate(
        _read_json(fixtures_dir / "destinations.json")
    ).destinations
    hotels = HotelsDocument.model_validate(_read_json(fixtures_dir / "hotels.json")).profiles
    route_rows = RoutesDocument.model_validate(_read_json(fixtures_dir / "routes.json")).routes
    pricing = PricingDocument.model_validate(_read_json(fixtures_dir / "pricing.json"))

    _unique_by_id(destinations, "id", "destinations.json")
    _unique_by_id(hotels, "slug", "hotels.json")
    _unique_by_id(route_rows, "destination_id", "routes.json")
    _unique_by_id(pricing.destinations, "destination_id", "pricing.json")

    destination_ids = {destination.id for destination in destinations}
    route_ids = {route.destination_id for route in route_rows}
    pricing_ids = {row.destination_id for row in pricing.destinations}
    if route_ids != destination_ids:
        raise ValueError("routes.json must contain exactly one route per destination")
    if pricing_ids != destination_ids:
        raise ValueError("pricing.json must contain exactly one tariff per destination")
    hotel_slugs = {hotel.slug for hotel in hotels}
    if set(pricing.hotel_levels) != hotel_slugs:
        raise ValueError("hotel_levels must contain exactly one multiplier per hotel profile")

    routes = {route.destination_id: route for route in route_rows}
    price_bands = {
        row.destination_id: DestinationPriceBand(
            destination_id=row.destination_id,
            hotel_nightly_3star=row.hotel_nightly_3star,
            flight_oneway_per_person=row.flight_oneway_per_person,
            transfer_oneway_group=row.transfer_oneway_group,
            pet_flight_surcharge=row.pet_flight_surcharge,
            monthly_factors=pricing.monthly_factors[row.seasonality],
        )
        for row in pricing.destinations
    }
    return CatalogFixtures(
        destinations=destinations,
        hotels=hotels,
        routes=routes,
        price_bands=price_bands,
        hotel_levels=pricing.hotel_levels,
    )
