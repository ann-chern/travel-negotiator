"""Deterministic ranking and safe, explicitly labelled compromise proposals.

No model inference, pricing fabrication, or mutation of the supplied request.
All results must pass the existing validator; unsupported constraints fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.data.catalog import destinations, generate_candidates
from app.schemas.request import Constraint, Operator, Strength, TripRequest
from app.schemas.trip import TripCandidate, ValidationResult
from app.services.validator import validate_trip


@dataclass(frozen=True)
class RankedTrip:
    trip: TripCandidate
    validation: ValidationResult
    score: float  # 0..1, illustrative heuristic, not model confidence
    matches: tuple[str, ...]
    tradeoffs: tuple[str, ...]


@dataclass(frozen=True)
class Compromise:
    description: str
    proposed_changes: tuple[str, ...]  # user must approve: never update TripRequest here
    trip: RankedTrip
    change_cost: float


@dataclass(frozen=True)
class SearchResult:
    status: str  # ready, needs_input, unsupported, no_match
    ranked: tuple[RankedTrip, ...] = ()
    compromises: tuple[Compromise, ...] = ()
    reasons: tuple[str, ...] = ()


SUPPORTED_FIELDS = frozenset(
    {
        "hotel.stars",
        "hotel.pets_allowed",
        "hotel.meal_plan",
        "hotel.free_cancellation",
        "transport.mode",
        "transport.stops",
        "transport.duration_minutes",
        "destination.country",
        "destination.tags",
        "trip.nights",
    }
)
# Catalog lacks verified center distances. Do not silently assume a number.
FIELD_ALIASES = {
    "hotel.distance_to_center_km": "Каталог не содержит проверенных расстояний до центра"
}


def _attribute(trip: TripCandidate, field: str, country_by_id: dict[str, str]) -> Any:
    if field == "hotel.stars":
        return trip.hotel.stars
    if field == "hotel.pets_allowed":
        return trip.hotel.pets_allowed
    if field == "hotel.meal_plan":
        return trip.hotel_offer.meal_plan
    if field == "hotel.free_cancellation":
        return trip.hotel_offer.free_cancellation
    if field == "transport.mode":
        return trip.outbound.mode if trip.outbound.mode == trip.inbound.mode else None
    if field == "transport.stops":
        return max(trip.outbound.stops, trip.inbound.stops)
    if field == "transport.duration_minutes":
        return max(
            int((leg.arrival_at - leg.departure_at).total_seconds() // 60)
            for leg in (trip.outbound, trip.inbound)
        )
    if field == "destination.country":
        return country_by_id.get(trip.destination_id)
    if field == "destination.tags":
        return next((d.tags for d in destinations() if d.id == trip.destination_id), set())
    if field == "trip.nights":
        return (trip.hotel_offer.check_out - trip.hotel_offer.check_in).days
    return None


def _compare(actual: Any, operator: Operator, target: Any) -> bool | None:
    if actual is None:
        return None
    if operator in (Operator.IN, Operator.NOT_IN):
        ok = bool(actual.intersection(target)) if isinstance(actual, set) else actual in target
        return ok if operator == Operator.IN else not ok
    if isinstance(actual, set):
        if operator == Operator.EQ:
            return target in actual
        if operator == Operator.NEQ:
            return target not in actual
        return None
    if operator == Operator.EQ:
        return actual == target
    if operator == Operator.NEQ:
        return actual != target
    if operator == Operator.GTE:
        return (
            actual >= target
            if isinstance(actual, (int, float)) and isinstance(target, (int, float))
            else None
        )
    if operator == Operator.LTE:
        return (
            actual <= target
            if isinstance(actual, (int, float)) and isinstance(target, (int, float))
            else None
        )
    if operator == Operator.BETWEEN:
        if not (
            isinstance(actual, (int, float))
            and isinstance(target, list)
            and len(target) == 2
            and all(isinstance(x, (int, float)) for x in target)
        ):
            return None
        return target[0] <= actual <= target[1]
    return None


def _matches(trip: TripCandidate, c: Constraint, countries: dict[str, str]) -> bool | None:
    return _compare(_attribute(trip, c.field, countries), c.operator, c.value)


def _score(
    trip: TripCandidate,
    validation: ValidationResult,
    request: TripRequest,
    countries: dict[str, str],
) -> RankedTrip:
    # No implicit star preference. Price tie-breaker is always available.
    total = validation.total_price
    assert total is not None
    # Normalization is deliberately bounded and independent of other candidates.
    price_score = float(Decimal("100000") / (Decimal("100000") + total.amount))
    matched: list[str] = []
    missed: list[str] = []
    pref_sum = 0.0
    weight_sum = 0.0
    weights = {"low": 1.0, "medium": 2.0, "high": 4.0}
    for pref in request.preferences:
        actual = _attribute(trip, pref.field, countries)
        ok = _compare(actual, Operator.EQ, pref.desired_value)
        w = weights[pref.importance.value]
        weight_sum += w
        if ok is True:
            matched.append(pref.field)
            pref_sum += w
        else:
            missed.append(
                f"{pref.field}: {'неизвестно' if ok is None else 'не соответствует пожеланию'}"
            )
    for c in request.constraints:
        if c.strength == Strength.SOFT:
            ok = _matches(trip, c, countries)
            weight_sum += 2
            if ok is True:
                matched.append(c.field)
                pref_sum += 2
            else:
                missed.append(
                    f"{c.field}: {'неизвестно' if ok is None else 'мягкое условие не выполнено'}"
                )
    score = 0.45 * price_score + 0.55 * (pref_sum / weight_sum) if weight_sum else price_score
    return RankedTrip(trip, validation, round(score, 6), tuple(matched), tuple(missed))


def _check_supported(request: TripRequest) -> tuple[str, ...]:
    reasons: list[str] = []
    for c in request.constraints:
        if c.field not in SUPPORTED_FIELDS:
            reasons.append(FIELD_ALIASES.get(c.field, f"Не поддерживается: {c.field}"))
        if c.field == "destination.tags" and c.operator not in (
            Operator.EQ,
            Operator.NEQ,
            Operator.IN,
            Operator.NOT_IN,
        ):
            reasons.append("Неподдерживаемый оператор для destination.tags")
    for p in request.preferences:
        if p.field not in SUPPORTED_FIELDS:
            reasons.append(FIELD_ALIASES.get(p.field, f"Не поддерживается: {p.field}"))
    for u in request.unresolved:
        reasons.append(f"Не удалось проверить требование: {u.text}")
    if request.date_window and request.departure_date is None:
        reasons.append("Подбор по диапазону дат пока не реализован")
    if request.return_date and request.departure_date and request.nights:
        from datetime import timedelta

        if request.return_date != request.departure_date + timedelta(days=request.nights):
            reasons.append("Несовместимые даты поездки и продолжительность")
    return tuple(dict.fromkeys(reasons))


def _rank_candidates(
    request: TripRequest,
    candidates: list[TripCandidate],
    *,
    ignored: frozenset[int] = frozenset(),
) -> list[RankedTrip]:
    countries = {d.id: d.location.country_code for d in destinations()}
    ranked = []
    for trip in candidates:
        validation = validate_trip(trip, request)
        # Only allow overriding the strict budget *for proposing*, never when accepting.
        if validation.status != "pass" or validation.total_price is None:
            continue
        if any(
            _matches(trip, c, countries) is not True
            for i, c in enumerate(request.constraints)
            if i not in ignored and c.strength == Strength.HARD
        ):
            continue
        ranked.append(_score(trip, validation, request, countries))
    return sorted(ranked, key=lambda r: (-r.score, r.validation.total_price.amount, r.trip.id))


def search_and_negotiate(request: TripRequest, *, limit: int = 3) -> SearchResult:
    """Returns safe ranked results and optional single-constraint relaxation suggestions.

    A hard budget is NEVER relaxed. Only explicitly marked `relaxation_allowed` hard
    constraints may be proposed, never automatically applied. Soft constraints are
    already ranking preferences and are not blocking filters.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    unsupported = _check_supported(request)
    if unsupported:
        return SearchResult("unsupported", reasons=unsupported)
    if (
        request.origin is None
        or request.departure_date is None
        or request.nights is None
        or request.travelers.adults is None
    ):
        return SearchResult(
            "needs_input",
            reasons=("Нужны город отправления, дата, ночи и число взрослых",),
        )
    if request.budget is not None and request.budget.scope != "whole_trip":
        return SearchResult(
            "unsupported",
            reasons=("Для MVP поддерживается бюджет всей поездки (whole_trip)",),
        )
    if request.budget is not None and request.budget.currency != "RUB":
        return SearchResult("unsupported", reasons=("Пока поддерживаются суммы в RUB",))
    candidates = generate_candidates(request, max_results=200)
    if not candidates:
        return SearchResult(
            "unsupported",
            reasons=(
                "Нет покрытия каталога для указанных направления, даты, "
                "отправления или состава гостей",
            ),
        )
    ranked = _rank_candidates(request, candidates)
    if ranked:
        return SearchResult("ready", ranked=tuple(ranked[:limit]))
    # Каждый компромисс повторно проходит валидацию без одного ограничения.
    compromises: list[Compromise] = []
    for i, c in enumerate(request.constraints):
        if c.strength != Strength.HARD or not c.relaxation_allowed:
            continue
        adjusted = request.model_copy(
            update={"constraints": [x for j, x in enumerate(request.constraints) if j != i]}
        )
        for result in _rank_candidates(adjusted, candidates)[:1]:
            countries = {d.id: d.location.country_code for d in destinations()}
            if _matches(result.trip, c, countries) is False:
                compromises.append(
                    Compromise(
                        description=(
                            f"Можно согласовать изменение условия {c.field} "
                            f"({c.operator.value} {c.value})"
                        ),
                        proposed_changes=(f"Изменить требование {c.field}",),
                        trip=result,
                        change_cost=1.0,
                    )
                )
    compromises.sort(
        key=lambda c: (
            c.change_cost,
            -c.trip.score,
            c.trip.validation.total_price.amount,
        )
    )
    return SearchResult(
        "no_match",
        compromises=tuple(compromises[:limit]),
        reasons=("Ни один вариант не удовлетворяет всем обязательным условиям",),
    )
