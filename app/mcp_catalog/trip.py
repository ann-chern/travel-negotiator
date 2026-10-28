"""Trip assembly contracts. All prices are synthetic or sourced, never inferred by an LLM."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.inventory import Hotel, HotelOffer, Money, TransferOffer, TransportOffer


class TripModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TripCandidate(TripModel):
    id: str = Field(min_length=1)
    destination_id: str = Field(min_length=1)
    hotel: Hotel
    hotel_offer: HotelOffer
    outbound: TransportOffer
    inbound: TransportOffer
    arrival_transfer: TransferOffer
    departure_transfer: TransferOffer


class ValidationIssue(TripModel):
    code: str
    status: Literal["fail", "unknown"]
    detail: str


class ValidationResult(TripModel):
    status: Literal["pass", "fail", "unknown"]
    issues: list[ValidationIssue] = Field(default_factory=list)
    total_price: Money | None = None
    breakdown: dict[str, Money] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_status(self) -> "ValidationResult":
        expected = "fail" if any(x.status == "fail" for x in self.issues) else ("unknown" if self.issues else "pass")
        if self.status != expected:
            raise ValueError("Validation status must agree with issues")
        return self
