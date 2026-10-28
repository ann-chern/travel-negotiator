"""Deterministic trip validation. No LLM calls; fail closed on missing information."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.schemas.inventory import Money
from app.schemas.request import TripRequest
from app.schemas.trip import TripCandidate, ValidationIssue, ValidationResult


def validate_trip(trip: TripCandidate, request: TripRequest | None = None) -> ValidationResult:
    issues: list[ValidationIssue] = []

    def add(code: str, status: str, detail: str) -> None:
        issues.append(ValidationIssue(code=code, status=status, detail=detail))

    offers = [
        trip.hotel_offer,
        trip.outbound,
        trip.inbound,
        trip.arrival_transfer,
        trip.departure_transfer,
    ]
    if (
        trip.hotel.destination_id != trip.destination_id
        or trip.hotel_offer.hotel_id != trip.hotel.id
    ):
        add(
            "hotel_destination_mismatch",
            "fail",
            "Hotel and stay must match destination and hotel ID",
        )

    for label, offer in zip(
        ("hotel", "outbound", "inbound", "arrival_transfer", "departure_transfer"),
        offers,
    ):
        if offer.availability != "available":
            add(
                f"{label}_availability",
                "fail" if offer.availability == "unavailable" else "unknown",
                f"{label} availability: {offer.availability}",
            )

    guest = trip.hotel_offer.occupancy
    parties = [trip.outbound.passengers, trip.inbound.passengers]
    if any(p != guest for p in parties):
        add(
            "passenger_mismatch",
            "fail",
            "Hotel and both transport offers must cover the same party",
        )
    if request:
        expected = request.travelers
        if expected.adults is not None and expected.adults != guest.adults:
            add("adult_count", "fail", "Offer adult count differs from request")
        if expected.children_ages_unknown:
            add(
                "child_ages",
                "unknown",
                "Children's ages are required to confirm occupancy and fares",
            )
        elif sorted(expected.children_ages) != sorted(guest.children_ages):
            add("children_count", "fail", "Offer children differ from request")
        if sum(p.count for p in expected.pets) != guest.pets_count:
            add("pet_count", "fail", "Offer pet count differs from request")

    people_count = guest.adults + len(guest.children_ages)
    for label, transfer in (
        ("arrival_transfer", trip.arrival_transfer),
        ("departure_transfer", trip.departure_transfer),
    ):
        if transfer.passenger_capacity < people_count:
            add(f"{label}_capacity", "fail", "Transfer cannot accommodate the party")

    if guest.pets_count:
        for label, allowed in (
            ("hotel", trip.hotel.pets_allowed),
            ("outbound", trip.outbound.pets_allowed),
            ("inbound", trip.inbound.pets_allowed),
            ("arrival_transfer", trip.arrival_transfer.pets_allowed),
            ("departure_transfer", trip.departure_transfer.pets_allowed),
        ):
            if allowed is not True:
                add(
                    f"{label}_pets",
                    "unknown" if allowed is None else "fail",
                    f"Pet permission for {label} is not confirmed",
                )

    if trip.outbound.destination_hub_id != trip.arrival_transfer.pickup_location_id:
        add(
            "arrival_pickup",
            "fail",
            "Arrival transfer does not start at inbound travel hub",
        )
    if trip.arrival_transfer.dropoff_location_id != trip.hotel.id:
        add("arrival_dropoff", "fail", "Arrival transfer must end at selected hotel")
    if trip.departure_transfer.pickup_location_id != trip.hotel.id:
        add(
            "departure_pickup",
            "fail",
            "Departure transfer must start at selected hotel",
        )
    if trip.departure_transfer.dropoff_location_id != trip.inbound.origin_hub_id:
        add(
            "departure_dropoff",
            "fail",
            "Departure transfer does not reach return transport hub",
        )
    if trip.outbound.origin_hub_id != trip.inbound.destination_hub_id:
        add("return_origin", "fail", "Round-trip transport returns to a different hub")

    if trip.arrival_transfer.pickup_at < trip.outbound.arrival_at + timedelta(minutes=30):
        add(
            "arrival_connection",
            "fail",
            "Less than 30 minutes between arrival and transfer pickup",
        )
    if trip.departure_transfer.arrival_at > trip.inbound.departure_at - timedelta(minutes=60):
        add(
            "departure_connection",
            "fail",
            "Less than 60 minutes between transfer arrival and return departure",
        )
    if trip.arrival_transfer.arrival_at.date() > trip.hotel_offer.check_in:
        add(
            "late_checkin",
            "unknown",
            "Late arrival requires confirmation of check-in arrangements",
        )
    if trip.departure_transfer.pickup_at.date() < trip.hotel_offer.check_out:
        add("early_checkout", "unknown", "Departure starts before paid checkout date")
    if trip.outbound.departure_at >= trip.inbound.departure_at:
        add(
            "travel_order",
            "fail",
            "Return transport must depart after outbound departure",
        )
    if trip.hotel_offer.check_out > trip.inbound.departure_at.date():
        add(
            "return_before_checkout",
            "unknown",
            "Hotel checkout is after departure date",
        )
    if request and request.nights is not None:
        if (trip.hotel_offer.check_out - trip.hotel_offer.check_in).days != request.nights:
            add("nights_mismatch", "fail", "Stay duration differs from requested nights")

    breakdown = {
        "outbound": trip.outbound.price,
        "inbound": trip.inbound.price,
        "hotel": trip.hotel_offer.price,
        "arrival_transfer": trip.arrival_transfer.price,
        "departure_transfer": trip.departure_transfer.price,
    }
    currencies = {m.currency for m in breakdown.values()}
    total = None
    if len(currencies) != 1:
        add(
            "mixed_currencies",
            "unknown",
            "FX conversion required before total and budget comparisons",
        )
    else:
        currency = next(iter(currencies))
        total = Money(
            amount=sum((m.amount for m in breakdown.values()), Decimal("0")),
            currency=currency,
        )
        if request and request.budget:
            budget = request.budget
            if budget.currency != currency:
                add(
                    "budget_currency",
                    "unknown",
                    "Budget currency differs from offer currency",
                )
            elif budget.scope != "whole_trip":
                add(
                    "budget_scope",
                    "unknown",
                    "Only whole_trip budget is validated at this stage",
                )
            elif budget.is_hard_limit and total.amount > budget.amount:
                add("budget_exceeded", "fail", "Trip exceeds strict budget")

    status = (
        "fail" if any(i.status == "fail" for i in issues) else ("unknown" if issues else "pass")
    )
    return ValidationResult(status=status, issues=issues, total_price=total, breakdown=breakdown)
