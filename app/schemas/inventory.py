"""Provider-independent travel inventory contracts (synthetic and future live offers)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InventoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceType(StrEnum):
    SYNTHETIC = "synthetic"
    EXTERNAL = "external"


class Provenance(InventoryModel):
    source_type: SourceType
    provider: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must have timezone information")
        return value


class Money(InventoryModel):
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        code = value.upper()
        if not code.isalpha():
            raise ValueError("currency must be an alphabetic three-letter code")
        return code


class Location(InventoryModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    country_code: str = Field(min_length=2, max_length=2)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone_name: str = Field(min_length=1)  # IANA, checked by ZoneInfo at load time

    @field_validator("country_code")
    @classmethod
    def country_upper(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("country code must be alphabetic")
        return value.upper()

    @field_validator("timezone_name")
    @classmethod
    def validate_iana_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class Destination(InventoryModel):
    id: str = Field(min_length=1)
    location: Location
    kind: Literal["city", "resort_area"] = "city"
    tags: set[str] = Field(default_factory=set)
    transport_hub_ids: list[str] = Field(default_factory=list)


class Hotel(InventoryModel):
    id: str = Field(min_length=1)
    destination_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    stars: int | None = Field(default=None, ge=1, le=5)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    pets_allowed: bool | None = None  # None = not known, not False
    amenities: set[str] = Field(default_factory=set)


class Occupancy(InventoryModel):
    adults: int = Field(ge=0, le=30)
    children_ages: list[int] = Field(default_factory=list)
    pets_count: int = Field(default=0, ge=0, le=10)

    @field_validator("children_ages")
    @classmethod
    def children_are_minors(cls, value: list[int]) -> list[int]:
        if any(age < 0 or age > 17 for age in value):
            raise ValueError("children ages must be 0..17")
        return value

    @model_validator(mode="after")
    def someone_travels(self) -> "Occupancy":
        if self.adults + len(self.children_ages) < 1:
            raise ValueError("at least one human guest required")
        return self


class HotelOffer(InventoryModel):
    id: str = Field(min_length=1)
    hotel_id: str = Field(min_length=1)
    check_in: date
    check_out: date
    occupancy: Occupancy
    room_name: str = Field(min_length=1)
    room_capacity: int = Field(ge=1)
    meal_plan: Literal["none", "breakfast", "half_board", "all_inclusive"]
    free_cancellation: bool | None = None
    price: Money  # full stay, entire occupancy, including known mandatory fees
    availability: Literal["available", "unavailable", "unknown"]
    provenance: Provenance

    @model_validator(mode="after")
    def check_stay(self) -> "HotelOffer":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be later than check_in")
        if self.occupancy.adults + len(self.occupancy.children_ages) > self.room_capacity:
            raise ValueError("occupancy exceeds room capacity")
        return self


class TransportOffer(InventoryModel):
    id: str = Field(min_length=1)
    origin_hub_id: str = Field(min_length=1)
    destination_hub_id: str = Field(min_length=1)
    mode: Literal["plane", "train"]
    departure_at: datetime
    arrival_at: datetime
    stops: int = Field(ge=0)
    passengers: Occupancy
    baggage_included: bool | None = None
    pets_allowed: bool | None = None
    price: Money  # full party, one direction, including known mandatory fees
    availability: Literal["available", "unavailable", "unknown"]
    provenance: Provenance

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def datetime_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def chronological_trip(self) -> "TransportOffer":
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival must occur after departure in absolute time")
        if self.origin_hub_id == self.destination_hub_id:
            raise ValueError("transport origin and destination must differ")
        return self


class TransferOffer(InventoryModel):
    id: str = Field(min_length=1)
    pickup_location_id: str = Field(min_length=1)
    dropoff_location_id: str = Field(min_length=1)
    pickup_at: datetime
    arrival_at: datetime
    passenger_capacity: int = Field(ge=1)
    luggage_capacity: int | None = Field(default=None, ge=0)
    pets_allowed: bool | None = None
    price: Money
    availability: Literal["available", "unavailable", "unknown"]
    provenance: Provenance

    @field_validator("pickup_at", "arrival_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("transfer times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_transfer(self) -> "TransferOffer":
        if self.arrival_at <= self.pickup_at:
            raise ValueError("transfer arrival must follow pickup")
        if self.pickup_location_id == self.dropoff_location_id:
            raise ValueError("transfer must connect distinct locations")
        return self
