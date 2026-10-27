"""Business logic behind stable MCP contracts.

This service does not scrape sites, accept payment or create real reservations.
Future catalogs should implement the same public shapes; do not expose raw
provider responses to the LLM.
"""
from __future__ import annotations
import hashlib
from pydantic import ValidationError

from app.data.catalog import generate_candidates
from app.mcp_catalog.contracts import (
    BookingResponse, CartValidationResponse, OfferDetailsResponse,
    OfferSummary, SearchOffersResponse,
)
from app.schemas.request import TripRequest
from app.services.optimizer import search_and_negotiate


def _parse_request(request_json: dict) -> TripRequest:
    return TripRequest.model_validate(request_json)


def _find(request: TripRequest, offer_id: str):
    # Ranked results already enforce HARD constraints and complete validation.
    ranked = search_and_negotiate(request, limit=200)
    return next((item for item in ranked.ranked if item.trip.id == offer_id), None)


def _summary(trip, validated) -> OfferSummary:
    return OfferSummary(
        offer_id=trip.id, destination_id=trip.destination_id,
        hotel_name=trip.hotel.name,
        departure_date=trip.hotel_offer.check_in.isoformat(),
        nights=(trip.hotel_offer.check_out - trip.hotel_offer.check_in).days,
        price_rub=str(validated.total_price.amount),
        availability="available", provenance="synthetic",
        observed_at=trip.hotel_offer.provenance.observed_at.isoformat(),
        is_bookable=False,
    )


class CatalogService:
    def search_offers(self, request_json: dict, limit: int = 10) -> SearchOffersResponse:
        if not 1 <= limit <= 100:
            return SearchOffersResponse(status="unsupported", message="limit must be 1..100")
        try:
            request = _parse_request(request_json)
        except (ValidationError, TypeError, ValueError) as exc:
            return SearchOffersResponse(status="unsupported", message=f"Invalid TripRequest: {exc}")
        missing = [x for x, absent in (("origin", not request.origin),
            ("departure_date", request.departure_date is None),
            ("nights", request.nights is None),
            ("travelers.adults", request.travelers.adults is None)) if absent]
        if missing:
            return SearchOffersResponse(status="needs_input", message="Missing: " + ", ".join(missing))
        result = search_and_negotiate(request, limit=200)
        if result.status in {"unsupported", "needs_input"}:
            return SearchOffersResponse(status=result.status, message="; ".join(result.reasons))
        available = [_summary(item.trip, item.validation) for item in result.ranked]
        available.sort(key=lambda row: (float(row.price_rub), row.offer_id))
        return SearchOffersResponse(status="ok", offers=available[:limit],
            message="DEMO ONLY: fictional prices and availability; not live inventory.")

    def get_offer_details(self, request_json: dict, offer_id: str) -> OfferDetailsResponse:
        try:
            request = _parse_request(request_json)
            trip = _find(request, offer_id)
        except (ValidationError, ValueError, TypeError) as exc:
            return OfferDetailsResponse(status="invalid_request", message=str(exc))
        if trip is None:
            return OfferDetailsResponse(status="not_found", message="Offer not present for this request")
        validation = trip.validation
        trip = trip.trip
        return OfferDetailsResponse(status="found", offer=_summary(trip, validation),
            hotel={"stars": trip.hotel.stars, "pets_allowed": trip.hotel.pets_allowed,
                   "meal_plan": trip.hotel_offer.meal_plan,
                   "free_cancellation": trip.hotel_offer.free_cancellation},
            transport={"outbound_stops": trip.outbound.stops, "inbound_stops": trip.inbound.stops,
                       "outbound_departure": trip.outbound.departure_at.isoformat(),
                       "inbound_departure": trip.inbound.departure_at.isoformat()},
            transfers={"arrival_transfer_id": trip.arrival_transfer.id,
                       "departure_transfer_id": trip.departure_transfer.id},
            message="Historical/synthetic price: not an actual bookable quote")

    def validate_cart(self, request_json: dict, offer_id: str) -> CartValidationResponse:
        try:
            request = _parse_request(request_json)
            trip = _find(request, offer_id)
        except (ValidationError, ValueError, TypeError) as exc:
            return CartValidationResponse(status="invalid_request", offer_id=offer_id, message=str(exc))
        if trip is None:
            return CartValidationResponse(status="not_found", offer_id=offer_id)
        result = trip.validation
        return CartValidationResponse(status=result.status, offer_id=offer_id,
            total_rub=str(result.total_price.amount) if result.total_price and result.total_price.currency == "RUB" else None,
            issues=[f"{issue.code}: {issue.detail}" for issue in result.issues],
            message="Validation is against a synthetic catalog, not a live booking engine")

    def simulate_booking(self, request_json: dict, offer_id: str, user_confirmed: bool = False) -> BookingResponse:
        if not user_confirmed:
            return BookingResponse(status="confirmation_required", offer_id=offer_id,
                message="Explicit user confirmation required. No booking has been created.")
        validation = self.validate_cart(request_json, offer_id)
        if validation.status != "pass":
            return BookingResponse(status="rejected", offer_id=offer_id,
                message=f"Cannot simulate booking: cart status {validation.status}")
        # Deterministic DEMO-only receipt, not a real reservation identifier.
        signature = hashlib.sha256((offer_id + "|" + str(validation.total_rub)).encode()).hexdigest()[:12]
        return BookingResponse(status="simulated", offer_id=offer_id,
            simulation_reference=f"DEMO-{signature}",
            message="DEMONSTRATION ONLY: no real hotel, flight, payment or booking was created.")
