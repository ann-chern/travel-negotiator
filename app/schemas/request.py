"""User intent contracts. No LLM or supplier-specific logic belongs here."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Strength(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class Source(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    DEFAULT = "default"


class Importance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Operator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"


# This is an allowlist: tools must not treat LLM-provided fields as executable paths.
ConstraintField = Literal[
    "hotel.stars",
    "hotel.pets_allowed",
    "hotel.meal_plan",
    "hotel.free_cancellation",
    "hotel.distance_to_center_km",
    "transport.mode",
    "transport.stops",
    "transport.duration_minutes",
    "destination.country",
    "destination.tags",
    "trip.nights",
]
ConstraintValue = Union[str, int, float, bool, list[str], list[int], list[float]]


class Constraint(StrictModel):
    field: ConstraintField
    operator: Operator
    value: ConstraintValue
    strength: Strength = Strength.HARD
    source: Source = Source.EXPLICIT
    relaxation_allowed: bool = False

    @model_validator(mode="after")
    def check_operator_value(self) -> "Constraint":
        if self.operator in (Operator.IN, Operator.NOT_IN, Operator.BETWEEN):
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("Collection operator requires a nonempty list")
            if self.operator == Operator.BETWEEN and len(self.value) != 2:
                raise ValueError("between requires exactly two bounds")
        elif isinstance(self.value, list):
            raise ValueError("Scalar operator requires a scalar value")
        return self


class Preference(StrictModel):
    field: ConstraintField
    desired_value: ConstraintValue
    importance: Importance = Importance.MEDIUM
    source: Source = Source.EXPLICIT


class Pet(StrictModel):
    kind: Literal["dog", "cat", "other"]
    count: int = Field(default=1, ge=1, le=10)
    notes: str | None = None


class Travelers(StrictModel):
    # None means unknown, not zero.
    adults: int | None = Field(default=None, ge=0, le=30)
    children_ages: list[int] = Field(default_factory=list)
    children_ages_unknown: int = Field(default=0, ge=0, le=30)
    pets: list[Pet] = Field(default_factory=list)
    rooms_requested: int | None = Field(default=None, ge=1, le=20)

    @field_validator("children_ages")
    @classmethod
    def check_ages(cls, ages: list[int]) -> list[int]:
        if any(age < 0 or age > 17 for age in ages):
            raise ValueError("Children must be aged 0–17")
        return ages


class Budget(StrictModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    scope: Literal["whole_trip", "per_person", "accommodation_only"] | None = None
    is_hard_limit: bool = True
    tolerance_fraction: float = Field(default=0.0, ge=0, le=0.5)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.upper()
        if not value.isalpha():
            raise ValueError("Currency must be an alphabetic ISO-like code")
        return value

    @model_validator(mode="after")
    def hard_budget_has_no_tolerance(self) -> "Budget":
        if self.is_hard_limit and self.tolerance_fraction != 0:
            raise ValueError("Hard budget cannot have tolerance")
        return self


class DateWindow(StrictModel):
    earliest_departure: date
    latest_departure: date

    @model_validator(mode="after")
    def ordered(self) -> "DateWindow":
        if self.earliest_departure > self.latest_departure:
            raise ValueError("Earliest departure must precede latest departure")
        return self


class UnresolvedRequirement(StrictModel):
    text: str = Field(min_length=1)
    reason: Literal["unknown_field", "missing_detail", "unsupported_by_catalog", "ambiguous"]


class TripRequest(StrictModel):
    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    return_date: date | None = None
    date_window: DateWindow | None = None
    nights: int | None = Field(default=None, ge=1, le=60)
    travelers: Travelers = Field(default_factory=Travelers)
    budget: Budget | None = None
    constraints: list[Constraint] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    unresolved: list[UnresolvedRequirement] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_dates(self) -> "TripRequest":
        if self.departure_date and self.return_date and self.return_date <= self.departure_date:
            raise ValueError("Return date must be after departure date")
        if self.departure_date and self.date_window:
            if (
                not self.date_window.earliest_departure
                <= self.departure_date
                <= self.date_window.latest_departure
            ):
                raise ValueError("Departure date is outside its date window")
        return self


# Explicit operations avoid confusing an absent field with a field to be cleared.
PatchableField = Literal[
    "origin",
    "destination",
    "departure_date",
    "return_date",
    "date_window",
    "nights",
    "travelers",
    "budget",
    "constraints",
    "preferences",
    "unresolved",
]


class SetOperation(StrictModel):
    op: Literal["set"]
    field: PatchableField
    value: object


class RemoveOperation(StrictModel):
    op: Literal["remove"]
    field: PatchableField


PatchOperation = Annotated[SetOperation | RemoveOperation, Field(discriminator="op")]


class RequestPatch(StrictModel):
    operations: list[PatchOperation] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_fields(self) -> "RequestPatch":
        fields = [item.field for item in self.operations]
        if len(fields) != len(set(fields)):
            raise ValueError("Each field may occur only once per patch")
        return self


def apply_request_patch(current: TripRequest, patch: RequestPatch) -> TripRequest:
    """Atomically apply changes; Pydantic validates the entire result.

    A failed patch does not mutate the existing request. Unknown fields are rejected.
    Nested values must be replaced as a whole in this first MVP version.
    """
    data = current.model_dump(mode="python")
    for operation in patch.operations:
        data[operation.field] = operation.value if operation.op == "set" else None
    return TripRequest.model_validate(data)
