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
    OfferSummary, SearchOffersResponse, NegotiationProposal, NegotiateOffersResponse,
)
from app.schemas.request import TripRequest
from app.services.validator import validate_trip
from app.services.optimizer import search_and_negotiate
from app.services.negotiation import search_with_plan_changes


def _parse_request(request_json: dict) -> TripRequest:
    return TripRequest.model_validate(request_json)


def _find(request: TripRequest, offer_id: str):
    # Important: search ALL candidate offers for the request; do not cap at 3.
    return next((x for x in generate_candidates(request, max_results=1000) if x.id == offer_id), None)


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
        hotel_stars=trip.hotel.stars,
        meal_plan=trip.hotel_offer.meal_plan,
        hotel_pets_allowed=trip.hotel.pets_allowed,
        outbound_stops=trip.outbound.stops,
        inbound_stops=trip.inbound.stops,
        adults=trip.hotel_offer.occupancy.adults,
        children_ages=list(trip.hotel_offer.occupancy.children_ages),
        pets_count=trip.hotel_offer.occupancy.pets_count,
    )


def _is_allowed_offer(request: TripRequest, offer_id: str) -> bool:
    """Authorize every offer action against the current request's hard constraints."""
    result = search_and_negotiate(request, limit=1000)
    return result.status == "ready" and any(row.trip.id == offer_id for row in result.ranked)


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
        # Do not bypass hard constraints: the optimizer is the source of truth.
        result = search_and_negotiate(request, limit=limit)
        if result.status != "ready":
            return SearchOffersResponse(
                status=result.status if result.status in {"unsupported", "needs_input", "no_match"} else "unsupported",
                message="; ".join(result.reasons) or "Нет предложений, удовлетворяющих всем условиям",
            )
        offers = [_summary(item.trip, item.validation) for item in result.ranked]
        return SearchOffersResponse(status="ok", offers=offers,
            message="DEMO ONLY: fictional prices and availability; not live inventory.")

    def search_date_window(self, request_json: dict, limit: int = 3) -> SearchOffersResponse:
        """Search EVERY day in a bounded window; one MCP call, read-only.

        Only verified catalog results are returned; never substitute a day outside
        the user's window. Prices are synthetic and not bookable.
        """
        from datetime import timedelta
        if not 1 <= limit <= 10:
            return SearchOffersResponse(status="unsupported", message="limit must be 1..10")
        try:
            request = _parse_request(request_json)
        except (ValidationError, TypeError, ValueError):
            return SearchOffersResponse(status="unsupported", message="Invalid TripRequest")
        if request.date_window is None or request.departure_date is not None:
            return SearchOffersResponse(status="needs_input", message="A date_window without departure_date is required")
        if not request.origin or request.nights is None or request.travelers.adults is None:
            return SearchOffersResponse(status="needs_input", message="Missing required travel information")
        first, last = request.date_window.earliest_departure, request.date_window.latest_departure
        count = (last - first).days + 1
        if count > 31:
            return SearchOffersResponse(status="unsupported", message="Диапазон больше 31 дня: уточните период")
        found: list[OfferSummary] = []
        had_no_match = False
        for offset in range(count):
            concrete = request.model_copy(update={"departure_date": first + timedelta(days=offset), "date_window": None})
            result = self.search_offers(concrete.model_dump(mode="json"), limit=limit)
            if result.status == "ok":
                found.extend(result.offers)
            elif result.status == "no_match":
                had_no_match = True
        if not found:
            return SearchOffersResponse(status="no_match" if had_no_match else "unsupported",
                message="В демо-каталоге нет подходящих предложений на даты выбранного периода")
        # Deterministic ranking across dates; stable tie-breakers.
        found.sort(key=lambda o: (float(o.price_rub), o.departure_date, o.offer_id))
        return SearchOffersResponse(status="ok", offers=found[:limit],
            message=f"Проверено {count} дат включительно; цены и наличие демонстрационные")

    def negotiate_offers(self, request_json: dict, *, allow_nights: bool = False,
                         allow_date_shift: bool = False, allow_budget_increase: bool = False,
                         limit: int = 3) -> NegotiateOffersResponse:
        """Read-only, verified proposals. Does NOT approve or change user's request."""
        if not 1 <= limit <= 10:
            return NegotiateOffersResponse(status="unsupported", message="limit must be 1..10")
        try:
            request = _parse_request(request_json)
        except (ValidationError, TypeError, ValueError):
            return NegotiateOffersResponse(status="unsupported", message="Invalid TripRequest")
        result = search_with_plan_changes(
            request, limit=limit, allow_nights=allow_nights,
            allow_date_shift=allow_date_shift, allow_budget_increase=allow_budget_increase,
        )
        status = result.original.status
        if status not in ("no_match", "ready"):
            return NegotiateOffersResponse(status=status if status in ("needs_input", "unsupported")
                else "unsupported", message="; ".join(result.original.reasons))
        if status == "ready":
            return NegotiateOffersResponse(status="ok",
                message="Исходный запрос уже имеет допустимые варианты. Уступки не требуются.")
        proposals = [NegotiationProposal(
            kind=alt.kind, description=alt.description,
            proposed_request=alt.proposed_request.model_dump(mode="json"),
            offer=_summary(alt.best_trip.trip, alt.best_trip.validation),
            penalty=alt.penalty, requires_confirmation=True,
        ) for alt in result.alternatives]
        return NegotiateOffersResponse(status="ok" if proposals else "no_match",
            proposals=proposals, message=("Предлагаемые изменения ещё не применены. Требуется согласие пользователя."
                if proposals else "Не найдено подходящих уступок в разрешённом диапазоне."))

    def get_offer_details(self, request_json: dict, offer_id: str) -> OfferDetailsResponse:
        try:
            request = _parse_request(request_json)
            trip = _find(request, offer_id)
        except (ValidationError, ValueError, TypeError) as exc:
            return OfferDetailsResponse(status="invalid_request", message=str(exc))
        if trip is None:
            return OfferDetailsResponse(status="not_found", message="Offer not present for this request")
        if not _is_allowed_offer(request, offer_id):
            return OfferDetailsResponse(status="not_found", message="Offer violates current constraints")
        validation = validate_trip(trip, request)
        if validation.status != "pass" or not validation.total_price:
            return OfferDetailsResponse(status="not_found", message="Offer does not pass validation")
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
        if not _is_allowed_offer(request, offer_id):
            return CartValidationResponse(status="not_found", offer_id=offer_id,
                message="Offer violates current request constraints")
        result = validate_trip(trip, request)
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
