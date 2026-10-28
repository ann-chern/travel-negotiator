"""Versioned public catalog contracts (all data is synthetic in this step)."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OfferSummary(Contract):
    offer_id: str
    destination_id: str
    hotel_name: str
    departure_date: str
    nights: int
    price_rub: str
    availability: Literal["available", "unavailable", "unknown"]
    provenance: Literal["synthetic"] = "synthetic"
    observed_at: str
    is_bookable: bool = False
    # Explicit, evidenced attributes; None means unknown, not false.
    hotel_stars: int | None = None
    meal_plan: Literal["none", "breakfast", "half_board", "all_inclusive"] | None = None
    hotel_pets_allowed: bool | None = None
    outbound_stops: int | None = Field(default=None, ge=0)
    inbound_stops: int | None = Field(default=None, ge=0)
    adults: int | None = None
    children_ages: list[int] = Field(default_factory=list)
    pets_count: int | None = None
    price_breakdown_rub: dict[str, str] = Field(default_factory=dict)
    match_score: int | None = Field(default=None, ge=0, le=100)
    match_reasons: list[str] = Field(default_factory=list)
    match_caveats: list[str] = Field(default_factory=list)


class SearchOffersResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["ok", "unsupported", "needs_input", "no_match"]
    offers: list[OfferSummary] = Field(default_factory=list)
    message: str = ""


class OfferDetailsResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["found", "not_found", "invalid_request"]
    offer: OfferSummary | None = None
    hotel: dict | None = None
    transport: dict | None = None
    transfers: dict | None = None
    message: str = ""


class CartValidationResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["pass", "fail", "unknown", "not_found", "invalid_request"]
    offer_id: str = ""
    total_rub: str | None = None
    issues: list[str] = Field(default_factory=list)
    message: str = ""


class BookingResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["confirmation_required", "simulated", "rejected"]
    offer_id: str = ""
    simulation_reference: str | None = None
    message: str
    real_booking_created: bool = False
    payment_collected: bool = False


class NegotiationProposal(Contract):
    kind: Literal["budget", "nights", "date"]
    description: str
    proposed_request: dict
    offer: OfferSummary
    penalty: float = Field(ge=0)
    requires_confirmation: Literal[True] = True


class NegotiateOffersResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["ok", "no_match", "needs_input", "unsupported"]
    proposals: list[NegotiationProposal] = Field(default_factory=list)
    message: str = ""
