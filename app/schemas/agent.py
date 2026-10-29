"""Модели обмена данными между слоями туристического агента."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.constants.agent import DEFAULT_AGENT_FLAGS
from app.schemas.request import DateWindow, TripRequest


class TurnIntent(StrEnum):
    PLAN = "plan"
    REVISE = "revise"
    OTHER = "other"


class TravelGoal(StrEnum):
    BEACH = "beach"
    CULTURE = "culture"
    NATURE = "nature"
    CITY = "city"
    FAMILY = "family"
    RELAX = "relax"


class OperationType(StrEnum):
    SET = "set"
    REMOVE = "remove"


class OperationField(StrEnum):
    ORIGIN = "origin"
    DESTINATION = "destination"
    DEPARTURE_DATE = "departure_date"
    NIGHTS = "nights"
    ADULTS = "adults"
    BUDGET_RUB = "budget_rub"
    CHILDREN_AGES = "children_ages"
    PETS = "pets"
    TRANSPORT_STOPS = "transport.stops"
    HOTEL_MEAL_PLAN = "hotel.meal_plan"


class PetKind(StrEnum):
    DOG = "dog"
    CAT = "cat"
    OTHER = "other"


class MealPlan(StrEnum):
    NONE = "none"
    BREAKFAST = "breakfast"
    HALF_BOARD = "half_board"
    ALL_INCLUSIVE = "all_inclusive"


class ClearConstraint(StrEnum):
    TRANSPORT_STOPS = "transport.stops"
    HOTEL_MEAL_PLAN = "hotel.meal_plan"


class ClearField(StrEnum):
    ORIGIN = "origin"
    DESTINATION = "destination"
    DEPARTURE_DATE = "departure_date"
    NIGHTS = "nights"
    BUDGET = "budget"
    PETS = "pets"
    CHILDREN = "children"


class DateKind(StrEnum):
    UNCHANGED = "unchanged"
    EXACT_STAY = "exact_stay"
    FLEXIBLE_DEPARTURE = "flexible_departure"


class DecisionKind(StrEnum):
    SEARCH = "search"
    CLARIFY = "clarify"
    CATALOG_GAP = "catalog_gap"


class TurnOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: OperationType
    field: OperationField
    value: str | int | bool | list[int] | None = Field(default=None)

    @model_validator(mode="after")
    def valid_value(self):
        if self.op == OperationType.REMOVE and self.value is not None:
            raise ValueError("remove must not supply value")
        if self.op == OperationType.SET and self.value is None:
            raise ValueError("set requires value")
        return self


class ParsedTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[TurnOperation] = Field(default_factory=list)
    origin: str | None = Field(default=None)
    destination: str | None = Field(default=None)
    departure_date: date | None = Field(default=None)
    nights: int | None = Field(default=None, ge=1, le=60)
    adults: int | None = Field(default=None, ge=1, le=30)
    budget_rub: int | None = Field(default=None, ge=1)
    budget_hard: bool | None = Field(default=None)
    travel_goal: TravelGoal | None = Field(default=None)
    children_ages: list[int] | None = Field(default=None)
    children_ages_unknown: int | None = Field(default=None, ge=0, le=30)
    pet_kind: PetKind | None = Field(default=None)
    pet_count: int | None = Field(default=None, ge=1, le=10)
    direct_flights: bool | None = Field(default=None)
    meal_plan: MealPlan | None = Field(default=None)
    clear_constraints: list[ClearConstraint] = Field(default_factory=list)
    clear_fields: list[ClearField] = Field(default_factory=list)
    allow_nights: bool = Field(default=False)
    allow_date_shift: bool = Field(default=False)
    allow_budget_increase: bool = Field(default=False)
    intent: TurnIntent = Field(default=TurnIntent.PLAN)
    unsupported_requirements: list[str] = Field(default_factory=list)


class CompactTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: TurnIntent = Field(default=TurnIntent.PLAN)
    operations: list[TurnOperation] = Field(default_factory=list)
    travel_goal: TravelGoal | None = Field(default=None)
    unsupported_requirements: list[str] = Field(default_factory=list)
    allow_nights: bool = Field(default=False)
    allow_date_shift: bool = Field(default=False)
    allow_budget_increase: bool = Field(default=False)

    @model_validator(mode="after")
    def non_conflicting(self):
        fields = [operation.field for operation in self.operations]
        if len(fields) != len(set(fields)):
            raise ValueError("Duplicate operations on one field")
        return self

    def as_parsed_turn(self) -> ParsedTurn:
        return ParsedTurn.model_validate(self.model_dump(mode="python"))


class Clarification(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    question: str
    missing_label: str
    reason: str


class DateInterpretation(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: DateKind
    window: DateWindow | None = Field(default=None)
    departure: date | None = Field(default=None)
    return_date: date | None = Field(default=None)
    nights: int | None = Field(default=None)


class PlanningDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: DecisionKind
    question: str | None = Field(default=None)
    missing_field: str | None = Field(default=None)


class TravelGraphState(BaseModel):
    user_message: str = Field(default="")
    request: TripRequest = Field(default_factory=TripRequest)
    flags: dict[str, bool] = Field(default_factory=lambda: dict(DEFAULT_AGENT_FLAGS))
    version: int = Field(default=0)
    stage: str = Field(default="new")
    pending_question: str | None = Field(default=None)
    options: list[dict[str, Any]] = Field(default_factory=list)
    compromises: list[dict[str, Any]] = Field(default_factory=list)
    response: str = Field(default="")
    error: str | None = Field(default=None)
    reasons: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    selected_offer_id: str | None = Field(default=None)
    selected_request_version: int | None = Field(default=None)
    selected_price_rub: str | None = Field(default=None)
    booking_reference: str | None = Field(default=None)
    compromise_request_version: int | None = Field(default=None)


class GraphReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    message: str
    version: int
    request: TripRequest
    options: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    compromises: tuple[dict[str, Any], ...] = Field(default_factory=tuple)


class Session(BaseModel):
    request: TripRequest = Field(default_factory=TripRequest)
    version: int = Field(default=0)
    search: Any | None = Field(default=None)
    flags: dict[str, bool] = Field(default_factory=lambda: dict(DEFAULT_AGENT_FLAGS))


class AgentReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    message: str
    version: int
    options: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    compromises: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
