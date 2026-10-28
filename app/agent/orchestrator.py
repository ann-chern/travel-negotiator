"""Bounded, deterministic agent control loop; does not book or mutate unapproved concessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.agent.llm_provider import ParsedTurn, TurnParser
from app.schemas.request import (
    Budget,
    Constraint,
    Operator,
    Pet,
    RemoveOperation,
    RequestPatch,
    SetOperation,
    Source,
    Strength,
    Travelers,
    TripRequest,
    UnresolvedRequirement,
    apply_request_patch,
)
from app.services.negotiation import NegotiatedSearch, search_with_plan_changes


@dataclass
class Session:
    request: TripRequest = field(default_factory=TripRequest)
    version: int = 0
    search: NegotiatedSearch | None = None
    flags: dict[str, bool] = field(
        default_factory=lambda: {
            "allow_nights": False,
            "allow_date_shift": False,
            "allow_budget_increase": False,
        }
    )


@dataclass(frozen=True)
class AgentReply:
    status: str
    message: str
    version: int
    options: tuple[dict, ...] = ()
    compromises: tuple[dict, ...] = ()


def _apply_explicit_operations(request: TripRequest, turn: ParsedTurn) -> TripRequest:
    """Validate and apply one-turn changes atomically without mutating request."""
    fields = [item.field for item in turn.operations]
    if len(fields) != len(set(fields)):
        raise ValueError("Duplicate or conflicting operations")
    # Reject ambiguous combinations of new operations and the legacy parser format.
    legacy = (
        "origin",
        "destination",
        "departure_date",
        "nights",
        "adults",
        "budget_rub",
        "children_ages",
        "children_ages_unknown",
        "pet_kind",
        "pet_count",
        "direct_flights",
        "meal_plan",
    )
    if (
        any(getattr(turn, name) is not None for name in legacy)
        or turn.clear_fields
        or turn.clear_constraints
    ):
        raise ValueError("Do not combine operations and legacy field edits")
    candidate = request
    for edit in turn.operations:
        name, value, remove = edit.field, edit.value, edit.op == "remove"
        if name in ("origin", "destination", "departure_date", "nights"):
            if not remove:
                if name in ("origin", "destination") and not isinstance(value, str):
                    raise ValueError(f"{name} requires text")
                if name == "nights" and (type(value) is not int or value < 1):
                    raise ValueError("nights requires positive integer")
            patch = RequestPatch(
                operations=[RemoveOperation(op="remove", field=name)]
                if remove
                else [SetOperation(op="set", field=name, value=value)]
            )
            candidate = apply_request_patch(candidate, patch)
        elif name == "budget_rub":
            candidate = _apply_turn(
                candidate,
                ParsedTurn(
                    clear_fields=["budget"] if remove else [],
                    budget_rub=None if remove else value,
                ),
            )
        elif name == "adults":
            if remove:
                travelers = Travelers.model_validate(
                    {**candidate.travelers.model_dump(), "adults": None}
                )
                candidate = apply_request_patch(
                    candidate,
                    RequestPatch(
                        operations=[SetOperation(op="set", field="travelers", value=travelers)]
                    ),
                )
            else:
                candidate = _apply_turn(candidate, ParsedTurn(adults=value))
        elif name == "children_ages":
            candidate = _apply_turn(
                candidate,
                ParsedTurn(
                    clear_fields=["children"] if remove else [],
                    children_ages=None if remove else value,
                ),
            )
        elif name == "pets":
            if remove:
                candidate = _apply_turn(candidate, ParsedTurn(clear_fields=["pets"]))
            else:
                if value not in ("dog", "cat", "other"):
                    raise ValueError("pets requires dog, cat or other")
                candidate = _apply_turn(candidate, ParsedTurn(pet_kind=value))
        elif name == "transport.stops":
            if remove:
                candidate = _apply_turn(candidate, ParsedTurn(clear_constraints=[name]))
            elif type(value) is int and value == 0:
                candidate = _apply_turn(candidate, ParsedTurn(direct_flights=True))
            else:
                raise ValueError("Only direct flights (=0) supported as a hard stop constraint")
        elif name == "hotel.meal_plan":
            if remove:
                candidate = _apply_turn(candidate, ParsedTurn(clear_constraints=[name]))
            else:
                candidate = _apply_turn(candidate, ParsedTurn(meal_plan=value))
    if (
        any(edit.field in ("departure_date", "nights") for edit in turn.operations)
        and request.return_date is not None
    ):
        from datetime import timedelta

        new_return = (
            candidate.departure_date + timedelta(days=candidate.nights)
            if candidate.departure_date and candidate.nights
            else None
        )
        candidate = apply_request_patch(
            candidate,
            RequestPatch(
                operations=[SetOperation(op="set", field="return_date", value=new_return)]
            ),
        )
    return candidate


def _apply_turn(request: TripRequest, turn: ParsedTurn) -> TripRequest:
    """Updates only specified fields; invalid mutations leave original untouched."""
    if turn.operations:
        return _apply_explicit_operations(request, turn)
    ops = []
    for field_name in ("origin", "destination", "departure_date", "nights"):
        value = getattr(turn, field_name)
        if value is not None:
            ops.append(SetOperation(op="set", field=field_name, value=value))
    traveler_fields = {}
    if turn.adults is not None:
        traveler_fields["adults"] = turn.adults
    if turn.children_ages is not None:
        traveler_fields["children_ages"] = turn.children_ages
    if turn.children_ages_unknown is not None:
        traveler_fields["children_ages_unknown"] = turn.children_ages_unknown
    if turn.pet_kind is not None:
        traveler_fields["pets"] = [Pet(kind=turn.pet_kind, count=turn.pet_count or 1)]
    elif turn.pet_count is not None:
        raise ValueError("pet_count requires pet_kind")
    if "children" in turn.clear_fields:
        if turn.children_ages is not None or turn.children_ages_unknown is not None:
            raise ValueError("Cannot set and clear children in one turn")
        traveler_fields.update(children_ages=[], children_ages_unknown=0)
    if "pets" in turn.clear_fields:
        if turn.pet_kind is not None:
            raise ValueError("Cannot set and clear pets in one turn")
        traveler_fields["pets"] = []
    if traveler_fields:
        travelers = Travelers.model_validate(
            {**request.travelers.model_dump(mode="python"), **traveler_fields}
        )
        ops.append(SetOperation(op="set", field="travelers", value=travelers))

    requested = set(turn.clear_constraints)
    if turn.direct_flights is not None:
        requested.add("transport.stops")
    if turn.meal_plan is not None:
        requested.add("hotel.meal_plan")
    if requested:
        constraints = [c for c in request.constraints if c.field not in requested]
        if turn.direct_flights is True:
            constraints.append(
                Constraint(
                    field="transport.stops",
                    operator=Operator.EQ,
                    value=0,
                    strength=Strength.HARD,
                    source=Source.EXPLICIT,
                )
            )
        if turn.meal_plan is not None:
            constraints.append(
                Constraint(
                    field="hotel.meal_plan",
                    operator=Operator.EQ,
                    value=turn.meal_plan,
                    strength=Strength.HARD,
                    source=Source.EXPLICIT,
                )
            )
        ops.append(SetOperation(op="set", field="constraints", value=constraints))
    if turn.budget_rub is not None:
        budget = Budget(
            amount=Decimal(turn.budget_rub),
            currency="RUB",
            scope="whole_trip",
            is_hard_limit=turn.budget_hard if turn.budget_hard is not None else True,
        )
        ops.append(SetOperation(op="set", field="budget", value=budget))
    for name in turn.clear_fields:
        if name in ("children", "pets"):
            continue
        if name in {op.field for op in ops}:
            raise ValueError(f"Contradictory patch: set and remove {name}")
        ops.append(RemoveOperation(op="remove", field=name))
    if turn.unsupported_requirements:
        unknown = list(request.unresolved) + [
            UnresolvedRequirement(text=t, reason="unsupported_by_catalog")
            for t in turn.unsupported_requirements
        ]
        ops.append(SetOperation(op="set", field="unresolved", value=unknown))
    next_request = apply_request_patch(request, RequestPatch(operations=ops))
    # Existing explicitly set return date must not silently become stale after a date/nights change.
    if (
        turn.departure_date is not None or turn.nights is not None
    ) and request.return_date is not None:
        from datetime import timedelta

        new_return = (
            next_request.departure_date + timedelta(days=next_request.nights)
            if next_request.departure_date and next_request.nights
            else None
        )
        next_request = apply_request_patch(
            next_request,
            RequestPatch(
                operations=[SetOperation(op="set", field="return_date", value=new_return)]
            ),
        )
    return next_request


class TravelAgent:
    def __init__(self, parser: TurnParser):
        self.parser = parser

    def handle(self, session: Session, message: str) -> AgentReply:
        """Transactional update; on LLM/search failures state remains unchanged."""
        if not message.strip():
            return AgentReply("error", "Напиши, какой отдых ты ищешь.", session.version)
        try:
            turn = self.parser.parse(message, session.request.model_dump(mode="json"))
            if turn.intent == "other":
                return AgentReply(
                    "other",
                    "Я помогаю планировать поездки. Опиши желаемую поездку.",
                    session.version,
                )
            candidate = _apply_turn(session.request, turn)
            flags = dict(session.flags)
            flags.update({name: flags[name] or getattr(turn, name) for name in flags})
            missing = []
            if not candidate.origin:
                missing.append("город отправления")
            if candidate.departure_date is None:
                missing.append("дату выезда")
            if candidate.nights is None:
                missing.append("количество ночей")
            if candidate.travelers.adults is None:
                missing.append("число взрослых")
            result = None if missing else search_with_plan_changes(candidate, **flags)
        except Exception as exc:
            # No exception details / secrets in UI; log in application layer.
            return AgentReply(
                "error",
                f"Не удалось обработать запрос ({type(exc).__name__}). "
                "Повтори или уточни формулировку.",
                session.version,
            )
        session.request = candidate
        session.flags = flags
        session.search = result
        session.version += 1
        if missing:
            return AgentReply(
                "needs_input",
                "Уточни, пожалуйста: " + ", ".join(missing) + ".",
                session.version,
            )
        assert result is not None
        if result.original.status != "ready":
            proposal_rows = tuple(
                {
                    "kind": a.kind,
                    "description": a.description,
                    "price_rub": str(a.best_trip.validation.total_price.amount),
                }
                for a in result.alternatives
            )
            reason = "; ".join(result.original.reasons) or "Нет подходящих предложений"
            return AgentReply(
                result.original.status,
                reason
                + (" Возможны изменения условий — выбери предложение." if proposal_rows else ""),
                session.version,
                compromises=proposal_rows,
            )
        options = tuple(
            {
                "id": x.trip.id,
                "destination": x.trip.destination_id,
                "hotel": x.trip.hotel.name,
                "price_rub": str(x.validation.total_price.amount),
                "score": x.score,
                "tradeoffs": list(x.tradeoffs),
                "synthetic": True,
            }
            for x in result.original.ranked
        )
        return AgentReply(
            "ready",
            "Вот доступные в демонстрационном каталоге варианты (цены синтетические).",
            session.version,
            options=options,
        )
