"""LangGraph orchestration over the existing deterministic Travel Negotiator core.

One shared model may serve specialized prompts. Agent never accesses catalog internals;
search is injected so a future MCP client can replace the local adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.agent.llm_provider import ParsedTurn, TurnParser, TurnOperation
from app.agent.orchestrator import _apply_turn
from app.agent.clarification import clarification_plan, select_clarification
from app.agent.intent_pipeline import interpret_calendar, apply_calendar, apply_travel_goal, DateKind, decide_next, DecisionKind
from app.agent.intent_rules import explicit_destination, trip_interval, shift_dates, unsupported_destination_message, reinforce_explicit_facts, destination_matches, reinforce_trip_duration
from app.schemas.request import RequestPatch, SetOperation
from app.schemas.request import TripRequest
from app.services.negotiation import NegotiatedSearch, search_with_plan_changes
from app.observability import event as audit_event


logger = logging.getLogger(__name__)


class TextGenerator(Protocol):
    def generate(self, *, task: str, payload: dict[str, Any]) -> str: ...


class SearchProvider(Protocol):
    def __call__(self, request: TripRequest, **kwargs: Any) -> NegotiatedSearch: ...


class TravelGraphState(BaseModel):
    """Per-thread state; only request changes from validated ParsedTurn are committed."""
    user_message: str = ""
    request: TripRequest = Field(default_factory=TripRequest)
    knowledge_hints: str = ""  # explicit user information only, never inferred facts
    flags: dict[str, bool] = Field(default_factory=lambda: {
        "allow_nights": False, "allow_date_shift": False, "allow_budget_increase": False
    })
    version: int = 0
    stage: str = "new"
    pending_question: str | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    compromises: list[dict[str, Any]] = Field(default_factory=list)
    response: str = ""
    error: str | None = None
    # Search outputs are transformed to JSON-safe rows; no unstable class instances in checkpoints.
    reasons: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    selected_offer_id: str | None = None
    selected_request_version: int | None = None
    selected_price_rub: str | None = None
    booking_reference: str | None = None
    compromise_request_version: int | None = None


@dataclass(frozen=True)
class GraphReply:
    status: str
    message: str
    version: int
    request: TripRequest
    options: tuple[dict[str, Any], ...]
    compromises: tuple[dict[str, Any], ...]


def _simple_constraint_revision(message: str) -> ParsedTurn | None:
    """Recognize only explicit, whole-message relaxation of two constraints.

    Do not guess user intentions from substrings of more complex instructions.
    """
    normalized = re.sub(r"[.!?]+$", "", message.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)
    # These two utterances are contractual changes, not requests for fresh travel data.
    if normalized in {
        "пересадку теперь можно, остальное не меняй",
        "пересадки теперь допустимы",
        "пересадки теперь можно",
        "теперь можно с пересадками",
    }:
        return ParsedTurn(intent="revise", clear_constraints=["transport.stops"])
    if normalized in {
        "и питание теперь необязательно",
        "питание теперь необязательно",
        "питание больше не важно",
        "питание больше не обязательно",
    }:
        return ParsedTurn(intent="revise", clear_constraints=["hotel.meal_plan"])
    return None


class GraphTravelAgent:
    """Graph runner. InMemorySaver persists state per thread ID while process lives."""

    def __init__(self, parser: TurnParser, writer: TextGenerator,
                 search: SearchProvider = search_with_plan_changes, checkpointer: Any = None, booking: Any = None):
        from langgraph.graph import END, START, StateGraph
        from langgraph.checkpoint.memory import InMemorySaver

        self.parser, self.writer, self.search = parser, writer, search
        self.booking = booking
        builder = StateGraph(TravelGraphState)
        builder.add_node("understand", self._understand_checkpoint_safe)
        builder.add_node("route", self._route)
        builder.add_node("clarify", self._clarify)
        builder.add_node("search", self._search)
        builder.add_node("recommend", self._recommend)
        builder.add_node("negotiate", self._negotiate)
        builder.add_node("cart_review", self._cart_review)
        builder.add_node("booking_confirmation", self._booking_confirmation)
        builder.add_edge(START, "understand")
        builder.add_edge("understand", "route")
        builder.add_conditional_edges("route", self._next, {
            "clarify": "clarify", "search": "search", "cart": "cart_review",
            "booking": "booking_confirmation", "end": END
        })
        builder.add_edge("clarify", END)
        builder.add_conditional_edges("search", self._after_search, {
            "recommend": "recommend", "negotiate": "negotiate"
        })
        builder.add_edge("recommend", END)
        builder.add_edge("negotiate", END)
        builder.add_edge("cart_review", END)
        builder.add_edge("booking_confirmation", END)
        self.graph = builder.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())

    @staticmethod
    def _state(state: Any) -> TravelGraphState:
        return TravelGraphState.model_validate(state)

    def _understand(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        if not s.user_message.strip():
            return {"stage": "error", "response": "Напиши, какой отдых ты ищешь."}
        msg = s.user_message.strip()
        normalized = msg.lower().strip().rstrip('.! ')
        if normalized in {'отмена', 'отменить', 'не бронировать', 'отказываюсь'}:
            return {'stage': 'cancelled', 'response': 'Выбор отменён. Демо-бронирование не выполнялось.',
                    'selected_offer_id': None, 'selected_request_version': None,
                    'selected_price_rub': None}
        if normalized in {'отклоняю компромиссы', 'не принимаю компромиссы'}:
            return {'stage': 'compromise_declined', 'response': 'Уступки отклонены. Исходные условия сохранены.',
                    'compromises': [], 'compromise_request_version': None}
        matched = re.fullmatch(r'принимаю компромисс ([1-3])', normalized)
        if matched:
            idx = int(matched.group(1)) - 1
            if (s.stage != 'negotiating' or s.compromise_request_version != s.version
                    or idx >= len(s.compromises)):
                return {'stage': 'error', 'response': 'Нет актуального компромисса с этим номером.'}
            proposal = s.compromises[idx]
            if proposal.get('requires_confirmation') is not True or not proposal.get('offer_id'):
                return {'stage': 'error', 'response': 'Предложение не прошло проверку контракта.'}
            try:
                candidate = TripRequest.model_validate(proposal['proposed_request'])
                kind = proposal.get('kind')
                permitted = {'nights': 'allow_nights', 'date': 'allow_date_shift',
                             'budget': 'allow_budget_increase'}
                if kind not in permitted or not s.flags.get(permitted[kind], False):
                    return {'stage': 'error', 'response': 'Эта уступка не была разрешена.'}
                before = s.request.model_dump(mode='json')
                after = candidate.model_dump(mode='json')
                differences = {key for key in before if before[key] != after[key]}
                allowed_changes = {'nights': {'nights', 'return_date'},
                                   'date': {'departure_date', 'return_date'},
                                   'budget': {'budget'}}[kind]
                if not differences or not differences.issubset(allowed_changes):
                    return {'stage': 'error', 'response': 'Предложение изменяет несогласованные условия.'}
                if kind == 'nights' and not (candidate.nights is not None and
                                               s.request.nights is not None and
                                               0 < candidate.nights < s.request.nights):
                    return {'stage': 'error', 'response': 'Некорректное изменение числа ночей.'}
                if kind == 'budget' and not (candidate.budget and s.request.budget and
                                               candidate.budget.amount > s.request.budget.amount):
                    return {'stage': 'error', 'response': 'Некорректное изменение бюджета.'}
                # Re-query the catalog through the same MCP boundary. Never accept a
                # proposal merely because a cached dictionary contains an offer ID.
                result = self.search(candidate)
                offered = next((x for x in result.options
                                if x.get('id') == proposal['offer_id'] and
                                str(x.get('price_rub')) == str(proposal['price_rub'])), None)
            except Exception:
                return {'stage': 'catalog_error', 'response': 'Не удалось повторно проверить уступку через каталог.'}
            if result.status != 'ready' or offered is None:
                return {'stage': 'compromise_stale', 'response': 'Уступка устарела или предложение изменилось. Выполни подбор снова.',
                        'compromises': [], 'compromise_request_version': None}
            return {'stage': 'searching', 'request': candidate.model_dump(mode='json'),
                    'version': s.version + 1, 'compromises': [], 'options': [],
                    'compromise_request_version': None, 'selected_offer_id': None,
                    'selected_request_version': None, 'selected_price_rub': None,
                    'booking_reference': None, 'response': ''}
        if normalized == 'подтверждаю демо-бронирование':
            if s.stage == 'awaiting_confirmation' and s.selected_offer_id and s.selected_request_version == s.version:
                return {'stage': 'confirming'}
            return {'stage': 'error', 'response': 'Нет актуальной корзины для подтверждения. Сначала выбери вариант.'}
        choice = self._selected_option(s.options, msg)
        if choice is not None:
            if s.request.departure_date is None and s.request.date_window is not None:
                from datetime import date
                try:
                    chosen_day = date.fromisoformat(choice['departure_date'])
                    if not (s.request.date_window.earliest_departure <= chosen_day <=
                            s.request.date_window.latest_departure):
                        raise ValueError('Selected date outside request window')
                    concrete = s.request.model_copy(update={"departure_date": chosen_day, "date_window": None})
                    concrete = TripRequest.model_validate(concrete.model_dump(mode="json"))
                except (ValueError, KeyError, TypeError):
                    return {'stage': 'error', 'response': 'Дата выбранного варианта не прошла проверку.'}
                return {'stage': 'cart_review', 'request': concrete.model_dump(mode='json'),
                        'version': s.version + 1, 'selected_offer_id': choice['id'],
                        'selected_request_version': s.version + 1, 'selected_price_rub': None,
                        'booking_reference': None}
            return {'stage': 'cart_review', 'selected_offer_id': choice['id'],
                    'selected_request_version': s.version, 'selected_price_rub': None,
                    'booking_reference': None}
        # A short numeric reply answers the active clarification, not a new intent.
        if (s.stage == "needs_input" and s.request.travelers.adults is None
                and s.pending_question and "взросл" in s.pending_question.lower()
                and re.fullmatch(r"[1-9]|[12][0-9]|30", normalized)):
            turn = ParsedTurn(intent="plan", adults=int(normalized))
            candidate = _apply_turn(s.request, turn)
            return {"request": candidate.model_dump(mode="json"), "version": s.version + 1,
                    "stage": "parsed", "pending_question": None, "options": [], "compromises": [],
                    "selected_offer_id": None, "selected_request_version": None,
                    "selected_price_rub": None, "booking_reference": None}
        # A relative date shift is a deterministic edit of the existing request.
        shifted = shift_dates(msg, s.request)
        if shifted is not None:
            return {"request": shifted.model_dump(mode="json"), "version": s.version + 1,
                    "stage": "parsed", "pending_question": None, "options": [], "compromises": [],
                    "selected_offer_id": None, "selected_request_version": None,
                    "selected_price_rub": None, "booking_reference": None}
        # Calendar is interpreted once, then merged with the facts of this turn.
        # A flexible month is already an answer: never ask for an exact day.
        calendar = interpret_calendar(msg, pending_question=s.pending_question)
        if calendar.kind != DateKind.UNCHANGED:
            # Explicit facts are collected independently of model response; for
            # unhandled semantics the normal LLM path below remains available.
            base = reinforce_trip_duration(msg, reinforce_explicit_facts(msg, s.request))
            # The parser may recognize soft trip goals and unfamiliar wording.
            # Explicit calendar semantics always win over a guessed exact date.
            try:
                turn = self.parser.parse(msg, base.model_dump(mode="json"))
                if turn.intent != "other":
                    if turn.operations:
                        turn = turn.model_copy(update={"operations": [op for op in turn.operations
                                  if op.field != "departure_date" and (calendar.kind != DateKind.EXACT_STAY or op.field != "nights")]})
                    else:
                        turn = turn.model_copy(update={"departure_date": None,
                        "nights": None if calendar.kind == DateKind.EXACT_STAY else turn.nights})
                    base = reinforce_trip_duration(msg, reinforce_explicit_facts(
                        msg, _apply_turn(base, turn)))
                    base = apply_travel_goal(base, turn.travel_goal)
            except Exception:
                logger.info("Semantic extraction unavailable; retaining explicit facts")
            candidate = apply_calendar(base, calendar)
            return {"request": candidate.model_dump(mode="json"), "version": s.version + 1,
                    "stage": "parsed", "options": [], "compromises": [], "pending_question": None,
                    "selected_offer_id": None, "selected_request_version": None,
                    "selected_price_rub": None, "compromise_request_version": None,
                    "booking_reference": None, "reasons": [], "error": None}
        # Fully explicit initial requests should not depend on LLM availability.
        if s.version == 0 and s.request == TripRequest():
            extracted = reinforce_trip_duration(msg, reinforce_explicit_facts(msg, s.request))
            if (extracted.origin is not None and extracted.travelers.adults is not None
                    and extracted.nights is not None):
                # Preserve open-ended intentions even when the essential fields
                # are already filled by explicit extraction. The LLM is optional:
                # failed extraction must not discard facts the user provided.
                try:
                    semantic = self.parser.parse(msg, extracted.model_dump(mode='json'))
                    if semantic.intent != 'other':
                        extracted = reinforce_trip_duration(msg, reinforce_explicit_facts(
                            msg, _apply_turn(extracted, semantic)))
                        extracted = apply_travel_goal(extracted, semantic.travel_goal)
                except Exception:
                    logger.info('Optional semantic extraction failed; explicit facts preserved')
                return {'request': extracted.model_dump(mode='json'), 'version': s.version + 1,
                        'stage': 'parsed', 'pending_question': None, 'options': [], 'compromises': [],
                        'selected_offer_id': None, 'selected_request_version': None,
                        'selected_price_rub': None, 'booking_reference': None}
        # Fully unambiguous removal of an existing constraint is a data operation,
        # not an open-ended language-generation task. All other text goes to LLM.
        turn = _simple_constraint_revision(msg)
        if turn is None:
            turn = self.parser.parse(s.user_message, s.request.model_dump(mode="json"))
        if turn.intent == "other":
            return {"stage": "other", "response": "Я помогаю планировать поездки. Опиши, какой отдых ты ищешь."}
        # Compute + validate before returning any state update; no partial patches.
        explicit_day = (turn.departure_date is not None or
                        any(op.field == "departure_date" and op.op == "set" for op in turn.operations))
        base_request = s.request
        if explicit_day and s.request.date_window is not None:
            # A precise new choice supersedes the earlier approximate window.
            base = s.request.model_dump(mode="python")
            base["date_window"] = None
            base_request = TripRequest.model_validate(base)
        # High-confidence explicit destination takes precedence over a missed LLM field.
        named_destination = explicit_destination(msg)
        if named_destination:
            if turn.operations:
                ops = [op for op in turn.operations if op.field != "destination"]
                ops.append(TurnOperation(
                    op="set", field="destination", value=named_destination))
                turn = turn.model_copy(update={"operations": ops})
            else:
                turn = turn.model_copy(update={"destination": named_destination})
        candidate = apply_travel_goal(
            reinforce_trip_duration(msg, reinforce_explicit_facts(msg, _apply_turn(base_request, turn))),
            turn.travel_goal)
        flags = dict(s.flags)
        for name in ("allow_nights", "allow_date_shift", "allow_budget_increase"):
            flags[name] = flags.get(name, False) or getattr(turn, name)
        return {"request": candidate, "flags": flags, "version": s.version + 1,
                "stage": "parsed", "pending_question": None,
                "options": [], "compromises": [], "compromise_request_version": None, "reasons": [], "error": None,
                "selected_offer_id": None, "selected_request_version": None,
                "selected_price_rub": None, "booking_reference": None}

    def _understand_checkpoint_safe(self, state: Any) -> dict[str, Any]:
        """Keep request as JSON primitives in LangGraph checkpoints.

        The Pydantic State model rehydrates this dict into TripRequest on input.
        This prevents LangGraph from deserializing arbitrary application classes.
        """
        before = self._state(state)
        audit_event("agent.node.started", node="understand", version=before.version)
        update = self._understand(state)
        request = update.get("request")
        if isinstance(request, TripRequest):
            update = {**update, "request": request.model_dump(mode="json")}
        audit_event("agent.message.interpreted", node="understand",
                    resulting_stage=update.get("stage", before.stage),
                    request_before=before.request,
                    request_after=update.get("request", before.request),
                    flags=update.get("flags", before.flags),
                    version=update.get("version", before.version))
        return update

    def _route(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        if s.stage in {"error", "other", "cart_review", "confirming", "cancelled", "compromise_declined", "compromise_stale"}:
            audit_event("agent.route.decided", decision="end", stage=s.stage)
            return {}
        decision = decide_next(s.request)
        if decision.kind == DecisionKind.CATALOG_GAP:
            audit_event("agent.route.decided", decision="unsupported", stage=s.stage)
            return {"stage": "unsupported", "response": unsupported_destination_message(s.request),
                    "options": [], "compromises": []}
        plan = clarification_plan(s.request)
        audit_event("agent.route.decided", decision=decision.kind.value,
                    next_node="clarify" if decision.kind == DecisionKind.CLARIFY else "search",
                    missing=[{"code": item.code, "label": item.missing_label,
                              "reason": item.reason} for item in plan])
        return {"stage": "needs_input" if decision.kind == DecisionKind.CLARIFY else "searching",
                "missing": [item.missing_label for item in plan]}

    @classmethod
    def _next(cls, state: Any) -> str:
        stage = cls._state(state).stage
        if stage == "cart_review": return "cart"
        if stage == "confirming": return "booking"
        return "end" if stage in ("error", "other", "cancelled", "compromise_declined", "compromise_stale", "unsupported") else "clarify" if stage == "needs_input" else "search"

    @staticmethod
    def _selected_option(options: list[dict[str, Any]], message: str) -> dict[str, Any] | None:
        # Intentionally narrow MVP grammar; do not interpret arbitrary prose as consent.
        m = re.fullmatch(r'(?:беру|выбираю|выбрать|хочу|покажи|оформи)\s+(?:вариант\s+)?(первый|второй|третий|[1-3])\s*(?:вариант)?[.!\s]*', message.lower().strip())
        if m:
            index = {'первый': 0, 'второй': 1, 'третий': 2, '1': 0, '2': 1, '3': 2}[m.group(1)]
            return options[index] if index < len(options) else None
        return next((o for o in options if message.strip() == o.get('id')), None)

    def _cart_review(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        if self.booking is None:
            return {'stage': 'catalog_error', 'response': 'Сервис проверки корзины не подключён.'}
        try:
            details = self.booking.details(s.request, s.selected_offer_id)
            validation = self.booking.validate(s.request, s.selected_offer_id)
        except Exception:
            return {'stage': 'catalog_error', 'response': 'Не удалось проверить корзину через MCP. Бронирование не выполнено.',
                    'selected_offer_id': None, 'selected_request_version': None}
        selected = next((o for o in s.options if o.get('id') == s.selected_offer_id), None)
        if (not selected or details.status != 'found' or details.offer is None
                or details.offer.offer_id != s.selected_offer_id
                or validation.status != 'pass' or not validation.total_rub
                or validation.offer_id != s.selected_offer_id
                or str(selected['price_rub']) != validation.total_rub):
            return {'stage': 'cart_rejected', 'response': 'Предложение больше не соответствует условиям или цене. Выполни поиск заново.',
                    'selected_offer_id': None, 'selected_request_version': None}
        return {'stage': 'awaiting_confirmation', 'selected_price_rub': validation.total_rub,
                'response': (f"Выбран вариант «{details.offer.hotel_name}» за {validation.total_rub} ₽. "
                             'Это синтетическая цена, реальная доступность не подтверждена. '
                             'Для создания исключительно демонстрационного подтверждения напиши '
                             '«подтверждаю демо-бронирование». Для отказа напиши «отмена».')}

    def _booking_confirmation(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        if (self.booking is None or not s.selected_offer_id or not s.selected_price_rub
                or s.selected_request_version != s.version):
            return {'stage': 'error', 'response': 'Корзина устарела. Выбери предложение заново.'}
        try:
            validation = self.booking.validate(s.request, s.selected_offer_id)
            if (validation.status != 'pass' or validation.offer_id != s.selected_offer_id
                    or validation.total_rub != s.selected_price_rub):
                return {'stage': 'cart_rejected', 'response': 'Условия или цена изменились. Демо-бронирование отменено.',
                        'selected_offer_id': None, 'selected_request_version': None}
            receipt = self.booking.simulate(s.request, s.selected_offer_id)
            if (receipt.status != 'simulated' or receipt.real_booking_created or receipt.payment_collected
                    or not receipt.simulation_reference):
                return {'stage': 'catalog_error', 'response': 'Не удалось создать демо-подтверждение.'}
        except Exception:
            return {'stage': 'catalog_error', 'response': 'Ошибка MCP при демонстрационном бронировании. Результат не подтверждён.'}
        return {'stage': 'booked_demo', 'booking_reference': receipt.simulation_reference,
                'selected_offer_id': None, 'selected_request_version': None, 'options': [],
                'response': (f'Демо-бронирование создано: {receipt.simulation_reference}. '
                             'Реальная бронь не оформлялась, оплата не производилась.')}

    def _clarify(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        focus = select_clarification(s.request)
        if focus is None:
            audit_event("agent.clarification.failed", reason="no_focus")
            return {"stage": "error", "response": "Не удалось определить уточнение."}
        fallback = focus.question
        prompt = {
            "user_message": s.user_message,
            "request": s.request.model_dump(mode="json"),
            "focus": {"code": focus.code, "reason": focus.reason},
            "fallback_question": fallback,
            "instruction": (
                "Сформулируй один короткий вопрос строго о поле focus. "
                "Не спрашивай о других полях, не назначай значения и не придумывай данные. "
                "Только текст вопроса, без вступления и списка."
            ),
        }
        used_fallback = False
        try:
            text = (fallback if s.request.date_window is not None else
                    self.writer.generate(task="clarification", payload=prompt).strip())
            # A verbose/malformed answer is less useful than a vetted question.
            if not text or len(text) > 240 or "\n" in text or "?" not in text:
                text = fallback
                used_fallback = True
        except Exception as exc:
            text = fallback
            used_fallback = True
            audit_event("agent.clarification.generator_failed", error_type=type(exc).__name__)
        audit_event("agent.clarification.asked", focus={"code": focus.code,
                    "missing_label": focus.missing_label, "reason": focus.reason},
                    question=text, used_fallback=used_fallback)
        return {"stage": "needs_input", "response": text,
                "pending_question": text}

    def _search(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        audit_event("agent.node.started", node="search", request=s.request, flags=s.flags)
        try:
            res = self.search(s.request, **s.flags)
        except Exception as exc:
            audit_event("agent.search.failed", error_type=type(exc).__name__)
            return {"stage": "catalog_error", "options": [], "compromises": [], "compromise_request_version": None,
                    "reasons": ["Каталог предложений временно недоступен. Попробуй ещё раз."],
                    "response": "Каталог предложений временно недоступен. Попробуй ещё раз."}
        # MCP adapter returns validated public tool data, not internal TripCandidate objects.
        from app.agent.mcp_search import McpSearchResult
        if isinstance(res, McpSearchResult):
            filtered = [o for o in res.options if destination_matches(s.request.destination, o.get('destination'))]
            status = res.status if filtered or res.status != 'ready' else 'no_match'
            reasons = res.reasons if filtered or res.status != 'ready' else ['Нет предложений для выбранного направления. Другие направления не подставляются автоматически.']
            audit_event("agent.search.completed", status=status,
                        received_options=len(res.options), accepted_options=len(filtered),
                        rejected_by_destination=len(res.options) - len(filtered),
                        option_ids=[item.get("id") for item in filtered], reasons=reasons,
                        compromises_count=len(res.compromises) if status == "negotiating" else 0)
            return {"stage": status, "options": filtered, "compromises": res.compromises if status == 'negotiating' else [],
                    "reasons": reasons,
                    "compromise_request_version": s.version if res.compromises else None}
        if res.original.status == "ready":
            options = [
                {"id": item.trip.id, "destination": item.trip.destination_id,
                 "hotel": item.trip.hotel.name, "price_rub": str(item.validation.total_price.amount),
                 "score": item.score, "tradeoffs": list(item.tradeoffs), "synthetic": True}
                for item in res.original.ranked
            ]
            options = [o for o in options if destination_matches(s.request.destination, o.get('destination'))]
            return {"stage": "ready" if options else "no_match", "options": options, "compromises": [],
                    "compromise_request_version": None,
                    "reasons": [] if options else ['Нет предложений для выбранного направления.']}
        proposals = [
            {"kind": alt.kind, "description": alt.description,
             "price_rub": str(alt.best_trip.validation.total_price.amount),
             "requires_confirmation": True}
            for alt in res.alternatives
        ]
        return {"stage": "negotiating" if proposals else res.original.status,
                "options": [], "compromises": proposals, "compromise_request_version": s.version if proposals else None,
                "reasons": list(res.original.reasons)}

    @classmethod
    def _after_search(cls, state: Any) -> str:
        return "recommend" if cls._state(state).stage == "ready" else "negotiate"

    def _recommend(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        fallback = "Найдены варианты в демонстрационном каталоге. Цены синтетические; выбери вариант из списка."
        try:
            text = self.writer.generate(task="recommendation", payload={
                "request": s.request.model_dump(mode="json"), "options": s.options,
                "instruction": "Объясни выбор кратко. Используй ТОЛЬКО эти варианты, цены и переданные свойства. "
                "В каждом варианте есть departure_date; обязательно укажи дату вылета при сравнении разных дат. "
                "Если meal_plan не null, тип питания подтверждён данными демо-каталога; если null — неизвестен. "
                "Не утверждай наличие характеристик, которых нет в options, и не обещай реальное бронирование."}).strip()
        except Exception as exc:
            text = fallback
            audit_event("agent.recommendation.generator_failed", error_type=type(exc).__name__)
        if s.request.date_window is not None and s.request.departure_date is None:
            dates = [o.get("departure_date") for o in s.options if o.get("departure_date")]
            if dates:
                dates_text = ", ".join(f"{i + 1}: {day}" for i, day in enumerate(dates))
                return {"response": (f"Даты вылета для предложенных вариантов ({dates_text}). "
                        + (text or fallback) + " Цены и наличие синтетические.")}
        response = text or fallback
        audit_event("agent.recommendation.completed", option_ids=[o.get("id") for o in s.options],
                    response=response)
        return {"response": response}

    def _negotiate(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        audit_event("agent.negotiation.started", stage=s.stage, reasons=s.reasons,
                    compromises=s.compromises)
        fallback = "; ".join(s.reasons) or "Нет подтверждённых подходящих вариантов."
        if s.stage == "catalog_error":
            return {"response": fallback}  # Never ask an LLM to fabricate unavailable offers.
        if s.compromises:
            fallback += " Доступны предложения изменить условия, но они требуют твоего согласия."
            fallback += " Для принятия напиши «принимаю компромисс 1» (или номер другого варианта); для отказа — «отклоняю компромиссы»."
        # A computed catalog price gap must not be replaced with vague LLM advice.
        if s.stage == "no_match" and any("Минимальная цена" in r for r in s.reasons):
            return {"response": fallback}
        if s.stage == "unsupported":
            return {"response": ("В демонстрационном каталоге нет покрытия для текущих условий. "
                    + ("Причина: " + "; ".join(s.reasons) + ". " if s.reasons else "")
                    + "Направление выбирать не обязательно, дата возврата и бюджет также необязательны. "
                    + "Можно выбрать другую точную дату вылета или уточнить условия; "
                    + "я не изменю требования без вашего указания.")}
        try:
            text = self.writer.generate(task="negotiation", payload={
                "request": s.request.model_dump(mode="json"), "reasons": s.reasons,
                "compromises": s.compromises,
                "instruction": "Не выдумывай варианты. Уступки не приняты: проси явного подтверждения."}).strip()
        except Exception:
            text = fallback
        if s.compromises:
            actions = " Для принятия напиши «принимаю компромисс 1» (или другой номер); для отказа — «отклоняю компромиссы»."
            text = (text or fallback) + actions
        response = text or fallback
        audit_event("agent.negotiation.completed", response=response,
                    requires_user_confirmation=bool(s.compromises))
        return {"response": response}

    def handle(self, thread_id: str, message: str) -> GraphReply:
        if not thread_id.strip():
            raise ValueError("thread_id cannot be empty")
        config = {"configurable": {"thread_id": thread_id}}
        # Keep last committed request when parser fails: graph nodes return updates atomically.
        try:
            output = self.graph.invoke({"user_message": message}, config=config)
        except Exception as exc:
            # Never log API keys, exception strings or raw user messages.
            error_class = type(exc).__name__
            # Never log raw prompts, exception strings, secrets, or model output.
            logger.warning("Graph turn failed at processing: %s", error_class)
            prior = self.graph.get_state(config).values
            s = self._state(prior)
            message_text = ("Сервис языковой модели не ответил вовремя. Повтори запрос." if
                            error_class in {"APITimeoutError", "APIConnectionError"} else
                            "Не удалось проверить изменения запроса. Переформулируй сообщение; прежние условия сохранены.")
            return GraphReply("error", message_text,
                              s.version, s.request, (), ())
        s = self._state(output)
        return GraphReply(s.stage, s.response, s.version, s.request,
                          tuple(s.options), tuple(s.compromises))

    def stream_updates(self, thread_id: str, message: str):
        """Yield completed LangGraph node progress and a single verified final reply.

        Unlike token-streaming this reports actual graph execution stages; structured
        offers are returned only after the graph finishes all validations.
        """
        if not thread_id.strip():
            raise ValueError("thread_id cannot be empty")
        config = {"configurable": {"thread_id": thread_id}}
        labels = {"understand": "Проверяю требования…", "route": "Определяю следующий шаг…",
                  "clarify": "Готовлю уточнение…", "search": "Проверяю предложения…",
                  "recommend": "Формирую рекомендацию…", "negotiate": "Проверяю альтернативы…",
                  "cart_review": "Проверяю корзину…",
                  "booking_confirmation": "Подтверждаю демо-бронирование…"}
        try:
            for update in self.graph.stream({"user_message": message}, config=config, stream_mode="updates"):
                for name in update:
                    if name in labels:
                        audit_event("agent.node.completed", node=name,
                                    update=list(update[name]) if isinstance(update[name], dict) else str(type(update[name]).__name__))
                        yield (name, labels[name])
            state = self._state(self.graph.get_state(config).values)
            yield ("done", GraphReply(state.stage, state.response, state.version, state.request,
                                      tuple(state.options), tuple(state.compromises)))
        except Exception as exc:
            logger.warning("Graph streaming turn failed: %s", type(exc).__name__)
            prior = self.graph.get_state(config).values
            state = self._state(prior)
            yield ("done", GraphReply("error", "Не удалось обработать сообщение. Прежние условия сохранены.",
                                      state.version, state.request, (), ()))
