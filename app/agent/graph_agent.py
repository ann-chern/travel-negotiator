"""LangGraph orchestration over the existing deterministic Travel Negotiator core.

One shared model may serve specialized prompts. Agent never accesses catalog internals;
search is injected so a future MCP client can replace the local adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.agent.llm_provider import ParsedTurn, TurnParser
from app.agent.orchestrator import _apply_turn
from app.schemas.request import TripRequest
from app.services.negotiation import NegotiatedSearch, search_with_plan_changes


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


@dataclass(frozen=True)
class GraphReply:
    status: str
    message: str
    version: int
    request: TripRequest
    options: tuple[dict[str, Any], ...]
    compromises: tuple[dict[str, Any], ...]


class GraphTravelAgent:
    """Graph runner. InMemorySaver persists state per thread ID while process lives."""

    def __init__(self, parser: TurnParser, writer: TextGenerator,
                 search: SearchProvider = search_with_plan_changes, checkpointer: Any = None):
        from langgraph.graph import END, START, StateGraph
        from langgraph.checkpoint.memory import InMemorySaver

        self.parser, self.writer, self.search = parser, writer, search
        builder = StateGraph(TravelGraphState)
        builder.add_node("understand", self._understand_checkpoint_safe)
        builder.add_node("route", self._route)
        builder.add_node("clarify", self._clarify)
        builder.add_node("search", self._search)
        builder.add_node("recommend", self._recommend)
        builder.add_node("negotiate", self._negotiate)
        builder.add_edge(START, "understand")
        builder.add_edge("understand", "route")
        builder.add_conditional_edges("route", self._next, {
            "clarify": "clarify", "search": "search", "end": END
        })
        builder.add_edge("clarify", END)
        builder.add_conditional_edges("search", self._after_search, {
            "recommend": "recommend", "negotiate": "negotiate"
        })
        builder.add_edge("recommend", END)
        builder.add_edge("negotiate", END)
        self.graph = builder.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())

    @staticmethod
    def _state(state: Any) -> TravelGraphState:
        return TravelGraphState.model_validate(state)

    def _understand(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        if not s.user_message.strip():
            return {"stage": "error", "response": "Напиши, какой отдых ты ищешь."}
        turn = self.parser.parse(s.user_message, s.request.model_dump(mode="json"))
        if turn.intent == "other":
            return {"stage": "other", "response": "Я помогаю планировать поездки. Опиши, какой отдых ты ищешь."}
        # Compute + validate before returning any state update; no partial patches.
        candidate = _apply_turn(s.request, turn)
        flags = dict(s.flags)
        for name in ("allow_nights", "allow_date_shift", "allow_budget_increase"):
            flags[name] = flags.get(name, False) or getattr(turn, name)
        return {"request": candidate, "flags": flags, "version": s.version + 1,
                "stage": "parsed", "pending_question": None,
                "options": [], "compromises": [], "reasons": [], "error": None}

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
        if s.stage in {"error", "other"}:
            return {}
        missing = []
        if not s.request.origin: missing.append("город отправления")
        if s.request.departure_date is None: missing.append("дату отправления")
        if s.request.nights is None: missing.append("продолжительность поездки")
        if s.request.travelers.adults is None: missing.append("число взрослых")
        return {"stage": "needs_input" if missing else "searching", "missing": missing}

    @classmethod
    def _next(cls, state: Any) -> str:
        stage = cls._state(state).stage
        return "end" if stage in ("error", "other") else "clarify" if stage == "needs_input" else "search"

    def _clarify(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        fallback = "Уточни, пожалуйста: " + ", ".join(s.missing) + "."
        prompt = {"user_message": s.user_message, "request": s.request.model_dump(mode="json"),
                  "missing": s.missing, "instruction": "Задай один короткий уточняющий вопрос. Не выдумывай данные."}
        try:
            text = self.writer.generate(task="clarification", payload=prompt).strip()
        except Exception:
            text = fallback
        return {"stage": "needs_input", "response": text or fallback, "pending_question": text or fallback}

    def _search(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        res = self.search(s.request, **s.flags)
        if res.original.status == "ready":
            options = [
                {"id": item.trip.id, "destination": item.trip.destination_id,
                 "hotel": item.trip.hotel.name, "price_rub": str(item.validation.total_price.amount),
                 "score": item.score, "tradeoffs": list(item.tradeoffs), "synthetic": True}
                for item in res.original.ranked
            ]
            return {"stage": "ready", "options": options, "compromises": [], "reasons": []}
        proposals = [
            {"kind": alt.kind, "description": alt.description,
             "price_rub": str(alt.best_trip.validation.total_price.amount),
             "requires_confirmation": True}
            for alt in res.alternatives
        ]
        return {"stage": "negotiating" if proposals else res.original.status,
                "options": [], "compromises": proposals, "reasons": list(res.original.reasons)}

    @classmethod
    def _after_search(cls, state: Any) -> str:
        return "recommend" if cls._state(state).stage == "ready" else "negotiate"

    def _recommend(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        fallback = "Найдены варианты в демонстрационном каталоге. Цены синтетические; выбери вариант из списка."
        try:
            text = self.writer.generate(task="recommendation", payload={
                "request": s.request.model_dump(mode="json"), "options": s.options,
                "instruction": "Объясни выбор кратко. Используй ТОЛЬКО эти варианты и цены; не обещай наличие или покупку."}).strip()
        except Exception:
            text = fallback
        return {"response": text or fallback}

    def _negotiate(self, state: Any) -> dict[str, Any]:
        s = self._state(state)
        fallback = "; ".join(s.reasons) or "Нет подтверждённых подходящих вариантов."
        if s.compromises:
            fallback += " Доступны предложения изменить условия, но они требуют твоего согласия."
        try:
            text = self.writer.generate(task="negotiation", payload={
                "request": s.request.model_dump(mode="json"), "reasons": s.reasons,
                "compromises": s.compromises,
                "instruction": "Не выдумывай варианты. Уступки не приняты: проси явного подтверждения."}).strip()
        except Exception:
            text = fallback
        return {"response": text or fallback}

    def handle(self, thread_id: str, message: str) -> GraphReply:
        if not thread_id.strip():
            raise ValueError("thread_id cannot be empty")
        config = {"configurable": {"thread_id": thread_id}}
        # Keep last committed request when parser fails: graph nodes return updates atomically.
        try:
            output = self.graph.invoke({"user_message": message}, config=config)
        except Exception:
            prior = self.graph.get_state(config).values
            s = self._state(prior)
            return GraphReply("error", "Не удалось обработать запрос. Повтори или уточни формулировку.",
                              s.version, s.request, tuple(s.options), tuple(s.compromises))
        s = self._state(output)
        return GraphReply(s.stage, s.response, s.version, s.request,
                          tuple(s.options), tuple(s.compromises))
