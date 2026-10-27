"""Bounded, deterministic agent control loop; does not book or mutate unapproved concessions."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.agent.llm_provider import ParsedTurn, TurnParser
from app.schemas.request import Budget, RequestPatch, SetOperation, RemoveOperation, TripRequest, Travelers, UnresolvedRequirement, apply_request_patch
from app.services.negotiation import search_with_plan_changes, NegotiatedSearch


@dataclass
class Session:
    request: TripRequest = field(default_factory=TripRequest)
    version: int = 0
    search: NegotiatedSearch | None = None
    flags: dict[str, bool] = field(default_factory=lambda: {
        "allow_nights": False, "allow_date_shift": False, "allow_budget_increase": False
    })
    pending_request: TripRequest | None = None


@dataclass(frozen=True)
class AgentReply:
    status: str
    message: str
    version: int
    options: tuple[dict, ...] = ()
    compromises: tuple[dict, ...] = ()


def _apply_turn(request: TripRequest, turn: ParsedTurn) -> TripRequest:
    """Updates only specified fields; invalid mutations leave original untouched."""
    ops = []
    for field in ("origin", "destination", "departure_date", "nights"):
        value = getattr(turn, field)
        if value is not None:
            ops.append(SetOperation(op="set", field=field, value=value))
    if turn.adults is not None:
        travelers = request.travelers.model_copy(update={"adults": turn.adults})
        ops.append(SetOperation(op="set", field="travelers", value=travelers))
    if turn.budget_rub is not None:
        budget = Budget(amount=Decimal(turn.budget_rub), currency="RUB", scope="whole_trip",
                        is_hard_limit=turn.budget_hard if turn.budget_hard is not None else True)
        ops.append(SetOperation(op="set", field="budget", value=budget))
    for name in turn.clear_fields:
        if name in {op.field for op in ops}:
            raise ValueError(f"Contradictory patch: set and remove {name}")
        ops.append(RemoveOperation(op="remove", field=name))
    if turn.unsupported_requirements:
        unknown = list(request.unresolved) + [UnresolvedRequirement(text=t, reason="unsupported_by_catalog")
                                              for t in turn.unsupported_requirements]
        ops.append(SetOperation(op="set", field="unresolved", value=unknown))
    next_request = apply_request_patch(request, RequestPatch(operations=ops))
    # Existing explicitly set return date must not silently become stale after a date/nights change.
    if (turn.departure_date is not None or turn.nights is not None) and request.return_date is not None:
        from datetime import timedelta
        new_return = (next_request.departure_date + timedelta(days=next_request.nights)
                      if next_request.departure_date and next_request.nights else None)
        next_request = apply_request_patch(next_request, RequestPatch(operations=[
            SetOperation(op="set", field="return_date", value=new_return)]))
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
                return AgentReply("other", "Я помогаю планировать поездки. Опиши желаемую поездку.", session.version)
            candidate = _apply_turn(session.request, turn)
            flags = dict(session.flags)
            flags.update({name: flags[name] or getattr(turn, name) for name in flags})
            missing = []
            if not candidate.origin: missing.append("город отправления")
            if candidate.departure_date is None: missing.append("дату выезда")
            if candidate.nights is None: missing.append("количество ночей")
            if candidate.travelers.adults is None: missing.append("число взрослых")
            result = None if missing else search_with_plan_changes(candidate, **flags)
        except Exception as exc:
            # No exception details / secrets in UI; log in application layer.
            return AgentReply("error", f"Не удалось обработать запрос ({type(exc).__name__}). Повтори или уточни формулировку.", session.version)
        session.request = candidate
        session.flags = flags
        session.search = result
        session.pending_request = None
        session.version += 1
        if missing:
            return AgentReply("needs_input", "Уточни, пожалуйста: " + ", ".join(missing) + ".", session.version)
        assert result is not None
        if result.original.status != "ready":
            proposal_rows = tuple({"kind": a.kind, "description": a.description,
                                  "price_rub": str(a.best_trip.validation.total_price.amount)}
                                  for a in result.alternatives)
            reason = "; ".join(result.original.reasons) or "Нет подходящих предложений"
            return AgentReply(result.original.status, reason + (" Возможны изменения условий — выбери предложение." if proposal_rows else ""),
                              session.version, compromises=proposal_rows)
        options = tuple({"id": x.trip.id, "destination": x.trip.destination_id,
                         "hotel": x.trip.hotel.name, "price_rub": str(x.validation.total_price.amount),
                         "score": x.score, "tradeoffs": list(x.tradeoffs), "synthetic": True}
                        for x in result.original.ranked)
        return AgentReply("ready", "Вот доступные в демонстрационном каталоге варианты (цены синтетические).", session.version,
                          options=options)
