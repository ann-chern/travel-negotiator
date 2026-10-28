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

from app.agent.llm_provider import ParsedTurn, TurnParser
from app.agent.orchestrator import _apply_turn
from app.agent.clarification import clarification_plan, select_clarification
from app.agent.date_intent import recognize_window
from app.schemas.request import RequestPatch, SetOperation
from app.schemas.request import TripRequest
from app.services.negotiation import NegotiatedSearch, search_with_plan_changes


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
        # Recognize explicit date ranges separately: they are NOT exact departure dates.
        window = recognize_window(msg)
        if window is not None:
            new_data = s.request.model_dump(mode="python")
            new_data["departure_date"] = None
            new_data["return_date"] = None
            new_data["date_window"] = window
            candidate = TripRequest.model_validate(new_data)
            return {"request": candidate.model_dump(mode="json"), "version": s.version + 1,
                    "stage": "parsed", "options": [], "compromises": [], "pending_question": None,
                    "selected_offer_id": None, "selected_request_version": None,
                    "selected_price_rub": None, "compromise_request_version": None,
                    "booking_reference": None, "reasons": [], "error": None}
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
        candidate = _apply_turn(base_request, turn)
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
        update = self._understand(state)
        request = update.get("request")
        if isinstance(request, TripRequest):
            update = {**update, "request": request.model_dump(mode="json")}
        return update

    def _route(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        if s.stage in {"error", "other", "cart_review", "confirming", "cancelled", "compromise_declined", "compromise_stale"}:
            return {}
        plan = clarification_plan(s.request)
        return {"stage": "needs_input" if plan else "searching",
                "missing": [item.missing_label for item in plan]}

    @classmethod
    def _next(cls, state: Any) -> str:
        stage = cls._state(state).stage
        if stage == "cart_review": return "cart"
        if stage == "confirming": return "booking"
        return "end" if stage in ("error", "other", "cancelled", "compromise_declined", "compromise_stale") else "clarify" if stage == "needs_input" else "search"

    @staticmethod
    def _selected_option(options: list[dict[str, Any]], message: str) -> dict[str, Any] | None:
        # Intentionally narrow MVP grammar; do not interpret arbitrary prose as consent.
        m = re.fullmatch(r'(?:беру|выбираю|хочу|покажи|оформи)\s+(первый|второй|третий|[1-3])\s*(?:вариант)?[.!\s]*', message.lower().strip())
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
        try:
            text = (fallback if s.request.date_window is not None else
                    self.writer.generate(task="clarification", payload=prompt).strip())
            # A verbose/malformed answer is less useful than a vetted question.
            if not text or len(text) > 240 or "\n" in text or "?" not in text:
                text = fallback
        except Exception:
            text = fallback
        return {"stage": "needs_input", "response": text,
                "pending_question": text}

    def _search(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        try:
            res = self.search(s.request, **s.flags)
        except Exception:
            return {"stage": "catalog_error", "options": [], "compromises": [], "compromise_request_version": None,
                    "reasons": ["Каталог предложений временно недоступен. Попробуй ещё раз."],
                    "response": "Каталог предложений временно недоступен. Попробуй ещё раз."}
        # MCP adapter returns validated public tool data, not internal TripCandidate objects.
        from app.agent.mcp_search import McpSearchResult
        if isinstance(res, McpSearchResult):
            return {"stage": res.status, "options": res.options, "compromises": res.compromises,
                    "reasons": res.reasons,
                    "compromise_request_version": s.version if res.compromises else None}
        if res.original.status == "ready":
            options = [
                {"id": item.trip.id, "destination": item.trip.destination_id,
                 "hotel": item.trip.hotel.name, "price_rub": str(item.validation.total_price.amount),
                 "score": item.score, "tradeoffs": list(item.tradeoffs), "synthetic": True}
                for item in res.original.ranked
            ]
            return {"stage": "ready", "options": options, "compromises": [], "compromise_request_version": None, "reasons": []}
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
        except Exception:
            text = fallback
        if s.request.date_window is not None and s.request.departure_date is None:
            dates = [o.get("departure_date") for o in s.options if o.get("departure_date")]
            if dates:
                dates_text = ", ".join(f"{i + 1}: {day}" for i, day in enumerate(dates))
                return {"response": (f"Даты вылета для предложенных вариантов ({dates_text}). "
                        + (text or fallback) + " Цены и наличие синтетические.")}
        return {"response": text or fallback}

    def _negotiate(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        fallback = "; ".join(s.reasons) or "Нет подтверждённых подходящих вариантов."
        if s.stage == "catalog_error":
            return {"response": fallback}  # Never ask an LLM to fabricate unavailable offers.
        if s.compromises:
            fallback += " Доступны предложения изменить условия, но они требуют твоего согласия."
            fallback += " Для принятия напиши «принимаю компромисс 1» (или номер другого варианта); для отказа — «отклоняю компромиссы»."
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
        return {"response": text or fallback}

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
