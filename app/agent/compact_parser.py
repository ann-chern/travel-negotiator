"""Small LLM-facing schema and bounded Yandex/DeepSeek parser.

No user request is mutated here. The existing orchestrator applies validated edits.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent.llm_provider import ParsedTurn, TurnOperation


class CompactTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["plan", "revise", "other"] = "plan"
    operations: list[TurnOperation] = Field(default_factory=list)
    travel_goal: Literal["beach", "culture", "nature", "city", "family", "relax"] | None = None
    unsupported_requirements: list[str] = Field(default_factory=list)
    allow_nights: bool = False
    allow_date_shift: bool = False
    allow_budget_increase: bool = False

    @model_validator(mode="after")
    def non_conflicting(self):
        fields = [op.field for op in self.operations]
        if len(fields) != len(set(fields)):
            raise ValueError("Duplicate operations on one field")
        return self

    def as_parsed_turn(self) -> ParsedTurn:
        return ParsedTurn.model_validate(self.model_dump(mode="python"))


SYSTEM_PROMPT = """Ты извлекаешь ИЗМЕНЕНИЯ требований к поездке. Ответь ТОЛЬКО компактным JSON объектом.
Ключи: intent (plan/revise/other), operations (массив), unsupported_requirements (массив строк), allow_nights, allow_date_shift, allow_budget_increase (булевы значения), travel_goal (beach/culture/nature/city/family/relax/null).
Операция: {"op":"set" или "remove", "field":"имя поля", "value":значение или null}.
Разрешены поля: origin, destination, departure_date, nights, adults, budget_rub, children_ages, pets, transport.stops, hotel.meal_plan.
Примеры:
«Мы вдвоём из Москвы» => {"intent":"plan","operations":[{"op":"set","field":"origin","value":"Москва"},{"op":"set","field":"adults","value":2}]}
«Пересадки теперь можно, завтрак оставь» => {"intent":"revise","operations":[{"op":"remove","field":"transport.stops","value":null}]}
«Питание больше не нужно» => {"intent":"revise","operations":[{"op":"remove","field":"hotel.meal_plan","value":null}]}
«Прямые рейсы» => {"intent":"revise","operations":[{"op":"set","field":"transport.stops","value":0}]}
«С собакой» => {"intent":"revise","operations":[{"op":"set","field":"pets","value":"dog"}]}
«С ребёнком 4 лет» => {"intent":"revise","operations":[{"op":"set","field":"children_ages","value":[4]}]}
«С завтраком» => {"intent":"revise","operations":[{"op":"set","field":"hotel.meal_plan","value":"breakfast"}]}
Дата departure_date: ISO YYYY-MM-DD, значение строкой. Не выдумывай отсутствующие факты.
Не дублируй текущее состояние в operations. Сохранить прежнее значение = НЕ возвращать операцию.
Если данных не хватает, оставь поле неизменным; не придумывай дату или город.
Цель отдыха извлекай по смыслу, например желание купаться => travel_goal=beach. Не пытайся приравнять любой город у моря к пляжному отдыху в любой сезон.
Диапазон («с 14 по 30 ноября») и приблизительная дата («во второй половине ноября») НЕ являются точной датой: не создавай операцию departure_date. Их обрабатывает отдельный модуль уточнений.
Если пользователь сообщает точный день и месяц, выбирай год из текущего состояния и текущего календарного года, не меняя день.
Не пиши рассуждения и markdown. Все неуказанные массивы пустые, флаги false.
"""


class CompactYandexTurnParser:
    """Drop-in TurnParser. Bounded call; no timeout retry, optional one JSON correction."""

    def __init__(self, *, model: str | None = None, api_key: str | None = None,
                 folder_id: str | None = None, client=None, timeout: float = 20.0,
                 max_tokens: int = 650, retry_invalid: bool = True):
        folder = folder_id or os.getenv("YANDEX_FOLDER_ID")
        if not folder:
            raise RuntimeError("YANDEX_FOLDER_ID is required")
        self.model = model or os.getenv("YANDEX_MODEL", "deepseek-v4.1-flash")
        if not self.model.startswith("gpt://"):
            self.model = f"gpt://{folder}/{self.model}"
        if client is None:
            key = api_key or os.getenv("YANDEX_API_KEY")
            if not key:
                raise RuntimeError("YANDEX_API_KEY is required")
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=os.getenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1"),
                            project=folder, max_retries=0, timeout=timeout)
        self.client = client
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.retry_invalid = retry_invalid
        self.last_diagnostic: dict = {}

    def parse(self, message: str, current_state: dict) -> ParsedTurn:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"today": date.today().isoformat(),
                "current_state": current_state, "message": message}, ensure_ascii=False, default=str)},
        ]
        for attempt in range(2 if self.retry_invalid else 1):
            started = time.monotonic()
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0,
                    response_format={"type": "json_object"}, max_tokens=self.max_tokens,
                    timeout=self.timeout,
                )
            except Exception as exc:
                self.last_diagnostic = {"attempt": attempt + 1, "error_type": type(exc).__name__,
                                        "elapsed_s": round(time.monotonic() - started, 3)}
                raise  # Timeout/auth/network: no implicit costly repeat
            choice = response.choices[0] if response.choices else None
            finish = getattr(choice, "finish_reason", None)
            content = (getattr(getattr(choice, "message", None), "content", None) or "").strip()
            usage = getattr(response, "usage", None)
            self.last_diagnostic = {"attempt": attempt + 1, "elapsed_s": round(time.monotonic() - started, 3),
                "finish_reason": finish, "nonempty": bool(content), "tokens_in": getattr(usage, "prompt_tokens", None),
                "tokens_out": getattr(usage, "completion_tokens", None)}
            try:
                if not content or finish == "length":
                    raise ValueError("Empty or truncated compact response")
                return CompactTurn.model_validate_json(content).as_parsed_turn()
            except (ValidationError, ValueError):
                self.last_diagnostic["error_type"] = "CompactValidationError"
                if attempt or not self.retry_invalid:
                    raise ValueError("Compact model response did not match validated operations contract") from None
                messages.append({"role": "user", "content":
                    "Верни заново короткий JSON с intent и operations; поле value обязательно, для remove оно null. "
                    "Не дублируй прежнее состояние. Не добавляй комментарии."})
        raise AssertionError("unreachable")
