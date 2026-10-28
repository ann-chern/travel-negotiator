"""Business logic behind stable MCP contracts.

This service does not scrape sites, accept payment or create real reservations.
Future catalogs should implement the same public shapes; do not expose raw
provider responses to the LLM.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from pydantic import ValidationError

from app.data.catalog import generate_candidates
from app.mcp_catalog.contracts import (
    BookingResponse,
    CartValidationResponse,
    NegotiateOffersResponse,
    NegotiationProposal,
    OfferDetailsResponse,
    OfferSummary,
    SearchOffersResponse,
)
from app.schemas.request import TripRequest
from app.services.negotiation import search_with_plan_changes
from app.services.optimizer import search_and_negotiate
from app.services.seasonality import OpenMeteoSeasonalityProvider, SeasonalityProvider
from app.services.validator import validate_trip


@lru_cache(maxsize=2)
def _seasonality_provider(mode: str):
    return (
        OpenMeteoSeasonalityProvider(allow_legacy_fallback=True)
        if mode == "openmeteo"
        else SeasonalityProvider()
    )


def _seasonality():
    # Read the JSON cache once per process; refresh it before starting Docker.
    return _seasonality_provider(os.environ.get("TN_SEASONALITY_PROVIDER", "legacy"))


def _parse_request(request_json: dict) -> TripRequest:
    return TripRequest.model_validate(request_json)


def _find(request: TripRequest, offer_id: str):
    return next(
        (x for x in generate_candidates(request, max_results=1000) if x.id == offer_id),
        None,
    )


def _summary(trip, validated) -> OfferSummary:
    return OfferSummary(
        offer_id=trip.id,
        destination_id=trip.destination_id,
        hotel_name=trip.hotel.name,
        departure_date=trip.outbound.departure_at.date().isoformat(),
        nights=(trip.hotel_offer.check_out - trip.hotel_offer.check_in).days,
        price_rub=str(validated.total_price.amount),
        price_breakdown_rub={key: str(money.amount) for key, money in validated.breakdown.items()},
        availability="available",
        provenance="synthetic",
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


def _portfolio(
    request: TripRequest,
    offers: list[OfferSummary],
    limit: int,
    *,
    flexible_dates: bool = False,
) -> list[OfferSummary]:
    """Choose distinct, *validated* offers after scoring, not merely cheapest rows.

    For an open destination prefer distinct cities. For a fixed destination offer
    a low-cost, a comfortable and a faster trip when feasible. For flexible dates,
    avoid three copies of the same trip on adjacent dates when alternatives are
    available. This function never changes prices or relaxes hard constraints.
    """
    if not offers:
        return []
    ranked = _rank_for_user(request, offers)
    chosen: list[OfferSummary] = []
    used: set[str] = set()

    def pick(label: str, candidates: list[OfferSummary]) -> None:
        for item in candidates:
            if item.offer_id in used:
                continue
            chosen.append(item.model_copy(update={"match_reasons": [label, *item.match_reasons]}))
            used.add(item.offer_id)
            return

    if request.destination is None:
        cities: set[str] = set()
        for candidate in ranked:
            if candidate.destination_id in cities:
                continue
            cities.add(candidate.destination_id)
            pick("Вариант другого направления", [candidate])
            if len(chosen) >= limit:
                break
    else:
        pick(
            "Самый доступный по цене",
            sorted(
                ranked,
                key=lambda o: (float(o.price_rub), -(o.match_score or 0), o.offer_id),
            ),
        )
        if len(chosen) < limit:
            # Метка комфорта требует реального улучшения категории или питания.
            baseline = chosen[0]
            meal_order = {
                "none": 0,
                "breakfast": 1,
                "half_board": 2,
                "full_board": 3,
                "all_inclusive": 4,
            }

            def comfort(o):
                return ((o.hotel_stars or 0), meal_order.get(o.meal_plan, 0))

            upgrades = [o for o in ranked if comfort(o) > comfort(baseline)]
            if upgrades:
                pick(
                    "Повышенный комфорт",
                    sorted(
                        upgrades,
                        key=lambda o: (
                            -(o.hotel_stars or 0),
                            -meal_order.get(o.meal_plan, 0),
                            -(o.match_score or 0),
                            float(o.price_rub),
                            o.offer_id,
                        ),
                    ),
                )
        if len(chosen) < limit:
            baseline_stops = (chosen[0].outbound_stops or 0) + (chosen[0].inbound_stops or 0)
            faster = [
                o
                for o in ranked
                if (o.outbound_stops or 0) + (o.inbound_stops or 0) < baseline_stops
            ]
            if faster:
                pick(
                    "Меньше пересадок",
                    sorted(
                        faster,
                        key=lambda o: (
                            (o.outbound_stops or 0) + (o.inbound_stops or 0),
                            -(o.match_score or 0),
                            float(o.price_rub),
                            o.offer_id,
                        ),
                    ),
                )

    while len(chosen) < min(limit, len(ranked)):

        def novelty(candidate: OfferSummary) -> tuple:
            if not chosen:
                return (0, 0, 0, 0)
            date_distance = min(
                abs(
                    (
                        __import__("datetime").date.fromisoformat(candidate.departure_date)
                        - __import__("datetime").date.fromisoformat(o.departure_date)
                    ).days
                )
                for o in chosen
            )
            new_destination = int(all(o.destination_id != candidate.destination_id for o in chosen))
            new_hotel = int(all(o.hotel_name != candidate.hotel_name for o in chosen))
            new_flight = int(
                all(
                    (o.outbound_stops, o.inbound_stops)
                    != (candidate.outbound_stops, candidate.inbound_stops)
                    for o in chosen
                )
            )
            return (
                new_destination,
                min(date_distance, 7) if flexible_dates else 0,
                new_hotel + new_flight,
                candidate.match_score or 0,
            )

        options = [o for o in ranked if o.offer_id not in used]
        if not options:
            break
        best = max(options, key=lambda o: (novelty(o), -float(o.price_rub), o.offer_id))
        pick("Альтернативный вариант", [best])
    return sorted(
        chosen[:limit],
        key=lambda o: (
            -(o.match_score if o.match_score is not None else -1),
            float(o.price_rub),
            o.departure_date,
            o.offer_id,
        ),
    )


def _assess_offer(request: TripRequest, offer: OfferSummary) -> OfferSummary:
    """Transparent preference-fit score, NOT quality, weather or booking probability.

    All scores operate only on fields verified in the synthetic catalog. Unknown
    seasonal conditions are disclosed rather than inferred from destination tags.
    """
    from app.data.catalog import active_cities

    tags = next(
        (city.tags for city in active_cities() if city.id == offer.destination_id),
        frozenset(),
    )
    score = 65
    reasons = ["Соответствует обязательным ограничениям поиска"]
    caveats = []
    for pref in request.preferences:
        if pref.field != "destination.tags":
            continue
        expected = pref.desired_value
        if isinstance(expected, str):
            if expected in ("beach", "sea", "warm"):
                # Destination type alone is insufficient to claim seasonal swim suitability.
                if expected in tags or "beach" in tags or "sea" in tags:
                    score += 14
                    reasons.append("Морское направление по профилю каталога (не гарантия купания)")
                else:
                    score -= 30
                    caveats.append("Не является профильным пляжным направлением")
            elif expected in tags:
                score += 20
                reasons.append("Направление соответствует заявленному типу отдыха (тег каталога)")
            else:
                score -= 20
                caveats.append("Тип отдыха не подтверждён тегами направления")
    if offer.outbound_stops == 0 and offer.inbound_stops == 0:
        score += 8
        reasons.append("Прямой перелёт в обе стороны")
    elif (offer.outbound_stops or 0) > 0 or (offer.inbound_stops or 0) > 0:
        caveats.append("Перелёт с пересадкой")
    if offer.meal_plan in ("breakfast", "half_board", "all_inclusive"):
        score += 5
        reasons.append("В тариф включено питание")
    elif offer.meal_plan == "none":
        caveats.append("Питание не включено")
    if request.budget is not None and request.budget.currency == "RUB":
        budget = request.budget.amount
        if request.budget.scope in (None, "whole_trip") and budget > 0:
            ratio = float(offer.price_rub) / float(budget)
            if ratio <= 0.75:
                score += 5
                reasons.append("Есть запас относительно бюджета")
    if any(
        p.field == "destination.tags" and p.desired_value in ("beach", "sea", "warm")
        for p in request.preferences
    ):
        from datetime import date

        seasonal = _seasonality().assess_beach(
            offer.destination_id, date.fromisoformat(offer.departure_date)
        )
        if seasonal.status == "favorable":
            score += 12
            reasons.append("Климатические нормы благоприятны: " + seasonal.summary)
        elif seasonal.status == "marginal":
            score -= 10
            caveats.append("Пограничный сезон: " + seasonal.summary)
        elif seasonal.status == "unfavorable":
            score -= 50
            caveats.append(
                "Пляжный отдых не рекомендуется по климатическим нормам: " + seasonal.summary
            )
        else:
            score -= 15
            caveats.append(seasonal.summary)
    caveats.append("Цены и доступность синтетические; реальное бронирование не предусмотрено")
    return offer.model_copy(
        update={
            "match_score": max(0, min(100, score)),
            "match_reasons": reasons,
            "match_caveats": caveats,
        }
    )


def _rank_for_user(request: TripRequest, offers: list[OfferSummary]) -> list[OfferSummary]:
    """Prefer suitability before price; deterministic tie-breaking."""
    scored = [_assess_offer(request, offer) for offer in offers]
    return sorted(
        scored,
        key=lambda o: (
            -int(o.match_score or 0),
            float(o.price_rub),
            o.departure_date,
            o.offer_id,
        ),
    )


class CatalogService:
    def search_offers(self, request_json: dict, limit: int = 10) -> SearchOffersResponse:
        if not 1 <= limit <= 100:
            return SearchOffersResponse(status="unsupported", message="limit must be 1..100")
        try:
            request = _parse_request(request_json)
        except (ValidationError, TypeError, ValueError) as exc:
            return SearchOffersResponse(status="unsupported", message=f"Invalid TripRequest: {exc}")
        missing = [
            x
            for x, absent in (
                ("origin", not request.origin),
                ("departure_date", request.departure_date is None),
                ("nights", request.nights is None),
                ("travelers.adults", request.travelers.adults is None),
            )
            if absent
        ]
        if missing:
            return SearchOffersResponse(
                status="needs_input", message="Missing: " + ", ".join(missing)
            )
        result = search_and_negotiate(request, limit=100)
        if result.status != "ready":
            return SearchOffersResponse(
                status=result.status
                if result.status in {"unsupported", "needs_input", "no_match"}
                else "unsupported",
                message="; ".join(result.reasons)
                or "Нет предложений, удовлетворяющих всем условиям",
            )
        offers = _portfolio(
            request,
            [_summary(item.trip, item.validation) for item in result.ranked],
            limit,
        )
        beach = any(
            p.field == "destination.tags" and p.desired_value in ("beach", "sea", "warm")
            for p in request.preferences
        )
        from datetime import date

        season_warning = ""
        if (
            beach
            and offers
            and not any(
                _seasonality()
                .assess_beach(o.destination_id, date.fromisoformat(o.departure_date))
                .status
                == "favorable"
                for o in offers
            )
        ):
            season_warning = (
                " ВНИМАНИЕ: ни один показанный вариант не подтверждён как "
                "подходящий для купания по среднемесячным климатическим данным; "
                "предложите смену месяца или направления."
            )
        return SearchOffersResponse(
            status="ok",
            offers=offers,
            message="DEMO ONLY: fictional prices and availability; not live inventory."
            + season_warning,
        )

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
            return SearchOffersResponse(
                status="needs_input",
                message="A date_window without departure_date is required",
            )
        if not request.origin or request.nights is None or request.travelers.adults is None:
            return SearchOffersResponse(
                status="needs_input", message="Missing required travel information"
            )
        first, last = (
            request.date_window.earliest_departure,
            request.date_window.latest_departure,
        )
        count = (last - first).days + 1
        if count > 31:
            return SearchOffersResponse(
                status="unsupported", message="Диапазон больше 31 дня: уточните период"
            )
        found: list[OfferSummary] = []
        had_no_match = False
        for offset in range(count):
            concrete = request.model_copy(
                update={
                    "departure_date": first + timedelta(days=offset),
                    "date_window": None,
                }
            )
            result = self.search_offers(concrete.model_dump(mode="json"), limit=100)
            if result.status == "ok":
                found.extend(result.offers)
            elif result.status == "no_match":
                had_no_match = True
        if not found:
            message = "В демо-каталоге нет подходящих предложений на даты выбранного периода"
            if had_no_match and request.budget is not None:
                # Порог цены рассчитывается по всему периоду без изменения бюджета.
                relaxed = request.model_copy(update={"budget": None})
                cheapest = None
                cheapest_day = None
                for offset in range(count):
                    trial = relaxed.model_copy(
                        update={
                            "departure_date": first + timedelta(days=offset),
                            "date_window": None,
                        }
                    )
                    from app.services.optimizer import search_and_negotiate

                    eligible = search_and_negotiate(trial, limit=200)
                    if eligible.status != "ready":
                        continue
                    for row in eligible.ranked:
                        amount = row.validation.total_price.amount
                        if cheapest is None or amount < cheapest:
                            cheapest, cheapest_day = amount, trial.departure_date
                if cheapest is not None:
                    deficit = max(0, cheapest - request.budget.amount)
                    message += (
                        f". Минимальная цена при сохранении остальных обязательных требований: "
                        f"{cheapest:,.0f} ₽ (вылет {cheapest_day.isoformat()}); "
                        f"бюджет {request.budget.amount:,.0f} ₽, не хватает {deficit:,.0f} ₽. "
                        "Можно согласовать увеличение бюджета, сокращение длительности "
                        "или другой период; ограничения не изменены"
                    )
            return SearchOffersResponse(
                status="no_match" if had_no_match else "unsupported", message=message
            )
        # Deterministic ranking across dates; stable tie-breakers.
        found = _rank_for_user(request, found)
        # For a clearly stated travel goal, never pad the UI with 0%-fit
        # alternatives solely to fill three cards. Returning fewer is valid.
        if request.preferences:
            relevant = [offer for offer in found if (offer.match_score or 0) > 0]
            if not relevant:
                return SearchOffersResponse(
                    status="no_match",
                    offers=[],
                    message=(
                        "Найдены поездки, но ни одна не соответствует заявленной цели отдыха. "
                        "Можно изменить период, направление или ослабить предпочтение; "
                        "условия не изменены."
                    ),
                )
            found = relevant
        selected = _portfolio(request, found, limit, flexible_dates=True)
        from datetime import date

        beach = any(
            p.field == "destination.tags" and p.desired_value in ("beach", "sea", "warm")
            for p in request.preferences
        )
        season_warning = ""
        if (
            beach
            and selected
            and not any(
                _seasonality()
                .assess_beach(o.destination_id, date.fromisoformat(o.departure_date))
                .status
                == "favorable"
                for o in selected
            )
        ):
            season_warning = (
                "; ни один показанный вариант не подтверждён как подходящий "
                "для купания по климатическим нормам"
            )
        return SearchOffersResponse(
            status="ok",
            offers=selected,
            message=f"Проверено {count} дат включительно; цены и наличие демонстрационные"
            + season_warning,
        )

    def negotiate_offers(
        self,
        request_json: dict,
        *,
        allow_nights: bool = False,
        allow_date_shift: bool = False,
        allow_budget_increase: bool = False,
        limit: int = 3,
    ) -> NegotiateOffersResponse:
        """Read-only, verified proposals. Does NOT approve or change user's request."""
        if not 1 <= limit <= 10:
            return NegotiateOffersResponse(status="unsupported", message="limit must be 1..10")
        try:
            request = _parse_request(request_json)
        except (ValidationError, TypeError, ValueError):
            return NegotiateOffersResponse(status="unsupported", message="Invalid TripRequest")
        result = search_with_plan_changes(
            request,
            limit=limit,
            allow_nights=allow_nights,
            allow_date_shift=allow_date_shift,
            allow_budget_increase=allow_budget_increase,
        )
        status = result.original.status
        if status not in ("no_match", "ready"):
            return NegotiateOffersResponse(
                status=status if status in ("needs_input", "unsupported") else "unsupported",
                message="; ".join(result.original.reasons),
            )
        if status == "ready":
            return NegotiateOffersResponse(
                status="ok",
                message="Исходный запрос уже имеет допустимые варианты. Уступки не требуются.",
            )
        proposals = [
            NegotiationProposal(
                kind=alt.kind,
                description=alt.description,
                proposed_request=alt.proposed_request.model_dump(mode="json"),
                offer=_summary(alt.best_trip.trip, alt.best_trip.validation),
                penalty=alt.penalty,
                requires_confirmation=True,
            )
            for alt in result.alternatives
        ]
        return NegotiateOffersResponse(
            status="ok" if proposals else "no_match",
            proposals=proposals,
            message=(
                "Предлагаемые изменения ещё не применены. Требуется согласие пользователя."
                if proposals
                else "Не найдено подходящих уступок в разрешённом диапазоне."
            ),
        )

    def get_offer_details(self, request_json: dict, offer_id: str) -> OfferDetailsResponse:
        try:
            request = _parse_request(request_json)
            trip = _find(request, offer_id)
        except (ValidationError, ValueError, TypeError) as exc:
            return OfferDetailsResponse(status="invalid_request", message=str(exc))
        if trip is None:
            return OfferDetailsResponse(
                status="not_found", message="Offer not present for this request"
            )
        if not _is_allowed_offer(request, offer_id):
            return OfferDetailsResponse(
                status="not_found", message="Offer violates current constraints"
            )
        validation = validate_trip(trip, request)
        if validation.status != "pass" or not validation.total_price:
            return OfferDetailsResponse(
                status="not_found", message="Offer does not pass validation"
            )
        return OfferDetailsResponse(
            status="found",
            offer=_summary(trip, validation),
            hotel={
                "stars": trip.hotel.stars,
                "pets_allowed": trip.hotel.pets_allowed,
                "meal_plan": trip.hotel_offer.meal_plan,
                "free_cancellation": trip.hotel_offer.free_cancellation,
            },
            transport={
                "outbound_stops": trip.outbound.stops,
                "inbound_stops": trip.inbound.stops,
                "outbound_departure": trip.outbound.departure_at.isoformat(),
                "inbound_departure": trip.inbound.departure_at.isoformat(),
            },
            transfers={
                "arrival_transfer_id": trip.arrival_transfer.id,
                "departure_transfer_id": trip.departure_transfer.id,
            },
            message="Historical/synthetic price: not an actual bookable quote",
        )

    def validate_cart(self, request_json: dict, offer_id: str) -> CartValidationResponse:
        try:
            request = _parse_request(request_json)
            trip = _find(request, offer_id)
        except (ValidationError, ValueError, TypeError) as exc:
            return CartValidationResponse(
                status="invalid_request", offer_id=offer_id, message=str(exc)
            )
        if trip is None:
            return CartValidationResponse(status="not_found", offer_id=offer_id)
        if not _is_allowed_offer(request, offer_id):
            return CartValidationResponse(
                status="not_found",
                offer_id=offer_id,
                message="Offer violates current request constraints",
            )
        result = validate_trip(trip, request)
        return CartValidationResponse(
            status=result.status,
            offer_id=offer_id,
            total_rub=str(result.total_price.amount)
            if result.total_price and result.total_price.currency == "RUB"
            else None,
            issues=[f"{issue.code}: {issue.detail}" for issue in result.issues],
            message="Validation is against a synthetic catalog, not a live booking engine",
        )

    def simulate_booking(
        self, request_json: dict, offer_id: str, user_confirmed: bool = False
    ) -> BookingResponse:
        if not user_confirmed:
            return BookingResponse(
                status="confirmation_required",
                offer_id=offer_id,
                message="Explicit user confirmation required. No booking has been created.",
            )
        validation = self.validate_cart(request_json, offer_id)
        if validation.status != "pass":
            return BookingResponse(
                status="rejected",
                offer_id=offer_id,
                message=f"Cannot simulate booking: cart status {validation.status}",
            )
        # Идентификатор относится только к демонстрационной операции.
        signature = hashlib.sha256(
            (offer_id + "|" + str(validation.total_rub)).encode()
        ).hexdigest()[:12]
        return BookingResponse(
            status="simulated",
            offer_id=offer_id,
            simulation_reference=f"DEMO-{signature}",
            message="DEMONSTRATION ONLY: no real hotel, flight, payment or booking was created.",
        )
