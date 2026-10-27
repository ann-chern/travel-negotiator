"""Deterministic proposals for budget, trip duration, and departure date.

Never modifies the original request. Every proposed alternative is re-searched and
validated under the proposed request. Proposals require explicit user approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from app.schemas.request import Budget, TripRequest
from app.services.optimizer import RankedTrip, SearchResult, search_and_negotiate

ChangeKind = Literal["budget", "nights", "date"]


@dataclass(frozen=True)
class PlanChange:
    kind: ChangeKind
    description: str
    proposed_request: TripRequest
    best_trip: RankedTrip
    penalty: float  # heuristic measure, not customer willingness or LLM confidence


@dataclass(frozen=True)
class NegotiatedSearch:
    original: SearchResult
    alternatives: tuple[PlanChange, ...] = ()


def _with(request: TripRequest, **changes: object) -> TripRequest:
    """Revalidate complete request; invalid proposals are never emitted."""
    data = request.model_dump(mode="python")
    data.update(changes)
    return TripRequest.model_validate(data)


def _budget_proposals(request: TripRequest, ceiling: Decimal) -> list[tuple[TripRequest, float, str]]:
    budget = request.budget
    if budget is None or budget.scope != "whole_trip" or budget.currency != "RUB":
        return []
    # A hard budget may be *proposed* for revision when explicitly enabled.
    # It remains hard in each proposed request, and is never applied to the original.
    if ceiling <= budget.amount:
        return []
    # At most +10%, +20%, +30%; all proposals shown as requiring approval.
    out = []
    for percentage in (10, 20, 30):
        amount = (budget.amount * (Decimal(100 + percentage) / 100)).quantize(Decimal("0.01"))
        if amount <= ceiling:
            new_budget = budget.model_copy(update={"amount": amount})
            out.append((_with(request, budget=new_budget), percentage / 10.0,
                        f"Предложить повысить мягкий бюджет до {amount} RUB (+{percentage}%)"))
    return out


def _night_proposals(request: TripRequest) -> list[tuple[TripRequest, float, str]]:
    if request.nights is None or request.nights <= 1:
        return []
    # Never change explicitly constrained trip duration without consent.
    if any(c.field == "trip.nights" and c.strength.value == "hard" for c in request.constraints):
        return []
    out = []
    for reduction in (1, 2, 3):
        if request.nights - reduction < 1:
            continue
        new_nights = request.nights - reduction
        new_return = request.departure_date + timedelta(days=new_nights) if request.return_date is not None and request.departure_date else None
        out.append((_with(request, nights=new_nights, return_date=new_return), float(reduction),
                    f"Предложить сократить проживание с {request.nights} до {new_nights} ночей"))
    return out


def _date_proposals(request: TripRequest) -> list[tuple[TripRequest, float, str]]:
    if request.departure_date is None:
        return []
    window = request.date_window
    if window is None:
        return []  # no user-authorized flexibility
    out = []
    for offset in (-3, -2, -1, 1, 2, 3):
        new_date = request.departure_date + timedelta(days=offset)
        if not window.earliest_departure <= new_date <= window.latest_departure:
            continue
        new_return = request.return_date + timedelta(days=offset) if request.return_date else None
        out.append((_with(request, departure_date=new_date, return_date=new_return),
                    abs(offset) * 0.6, f"Предложить перенести отправление на {new_date.isoformat()}"))
    return out


def search_with_plan_changes(
    request: TripRequest, *, limit: int = 3,
    allow_nights: bool = False,
    allow_date_shift: bool = False,
    allow_budget_increase: bool = False,
    max_extra_budget_fraction: Decimal = Decimal("0.30"),
) -> NegotiatedSearch:
    """Extend existing search with optional explicitly authorized negotiations.

    The flags reflect explicit user permission/agent-collected consent to *suggest*
    changes (not to enact them). If source lacks flexible dates or budget, no
    proposals of those types are made. Never returns a failing trip as valid.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if not Decimal("0") <= max_extra_budget_fraction <= Decimal("0.30"):
        raise ValueError("max_extra_budget_fraction must be between 0 and 0.30")
    original = search_and_negotiate(request, limit=limit)
    if original.status != "no_match":
        return NegotiatedSearch(original)

    trials: list[tuple[TripRequest, float, str, ChangeKind]] = []
    if allow_nights:
        trials.extend((r, penalty, desc, "nights") for r, penalty, desc in _night_proposals(request))
    if allow_date_shift:
        trials.extend((r, penalty, desc, "date") for r, penalty, desc in _date_proposals(request))
    if allow_budget_increase and request.budget:
        cap = (request.budget.amount * (1 + max_extra_budget_fraction)).quantize(Decimal("0.01"))
        trials.extend((r, penalty, desc, "budget") for r, penalty, desc in _budget_proposals(request, cap))

    results: list[PlanChange] = []
    for proposed_request, penalty, description, kind in trials:
        search = search_and_negotiate(proposed_request, limit=1)
        if search.status == "ready" and search.ranked and search.ranked[0].validation.status == "pass":
            # This result comes from a separate search over a valid proposed request.
            results.append(PlanChange(kind, description, proposed_request, search.ranked[0], penalty))
    results.sort(key=lambda item: (item.penalty, -item.best_trip.score, item.best_trip.validation.total_price.amount, item.kind))
    return NegotiatedSearch(original, tuple(results[:limit]))
