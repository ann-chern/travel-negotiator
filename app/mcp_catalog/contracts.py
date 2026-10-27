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


class SearchOffersResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["ok", "unsupported", "needs_input"]
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
