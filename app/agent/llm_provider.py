"""Structured extraction using an optional OpenAI-compatible LLM endpoint."""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError


class TurnOperation(BaseModel):
    """Atomic user-intended edit of one allowlisted concept.

    No arbitrary object paths or executable tool names are accepted.
    """
    model_config = ConfigDict(extra="forbid")
    op: Literal["set", "remove"]
    field: Literal["origin", "destination", "departure_date", "nights", "adults",
                   "budget_rub", "children_ages", "pets", "transport.stops", "hotel.meal_plan"]
    value: str | int | bool | list[int] | None = None

    @model_validator(mode="after")
    def valid_value(self):
        if self.op == "remove" and self.value is not None:
            raise ValueError("remove must not supply value")
        if self.op == "set" and self.value is None:
            raise ValueError("set requires value")
        return self


class ParsedTurn(BaseModel):
    """LLM output: only changed values; null means 'not mentioned'.

    To remove a field, use clear_fields. A real removal is an explicit operation.
    """
    model_config = ConfigDict(extra="forbid")
    operations: list[TurnOperation] = Field(default_factory=list)
    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    nights: int | None = Field(default=None, ge=1, le=60)
    adults: int | None = Field(default=None, ge=1, le=30)
    budget_rub: int | None = Field(default=None, ge=1)
    budget_hard: bool | None = None
    travel_goal: Literal["beach", "culture", "nature", "city", "family", "relax"] | None = None
    # Null = unchanged. Explicit deletion uses clear_fields/clear_constraints.
    children_ages: list[int] | None = None
    children_ages_unknown: int | None = Field(default=None, ge=0, le=30)
    pet_kind: Literal["dog", "cat", "other"] | None = None
    pet_count: int | None = Field(default=None, ge=1, le=10)
    direct_flights: bool | None = None
    meal_plan: Literal["none", "breakfast", "half_board", "all_inclusive"] | None = None
    clear_constraints: list[Literal["transport.stops", "hotel.meal_plan"]] = Field(default_factory=list)
    clear_fields: list[Literal["origin", "destination", "departure_date", "nights", "budget", "pets", "children"]] = Field(default_factory=list)
    allow_nights: bool = False
    allow_date_shift: bool = False
    allow_budget_increase: bool = False
    intent: Literal["plan", "revise", "other"] = "plan"
    unsupported_requirements: list[str] = Field(default_factory=list)


class TurnParser(Protocol):
    def parse(self, message: str, current_state: dict) -> ParsedTurn: ...


INSTRUCTIONS = """Ты — парсер изменений запроса туриста. Отвечай JSON-объектом с полями ParsedTurn.
Извлекай только изменённые требования, не угадывай недостающие данные.
Предпочитай один формат: operations или legacy, никогда не смешивай их.
Для operations используй op=set/remove, field, value (для remove null).
Учитывай явно заданные требования к детям, питомцам, пересадкам и питанию.
Если пользователь назвал цель отдыха, верни travel_goal: beach (пляж, купание), culture, nature, city, family или relax.
Цель поездки — предпочтение, не жёсткая фильтрация. Если цель не ясна, верни null.
"""


class YandexTurnParser:
    def __init__(self, *, model: str | None = None, api_key: str | None = None,
                 folder_id: str | None = None, client=None):
        key = api_key or os.getenv("YANDEX_API_KEY")
        folder = folder_id or os.getenv("YANDEX_FOLDER_ID")
        if not folder:
            raise RuntimeError("YANDEX_FOLDER_ID is required for Agent Mode")
        if client is None:
            if not key:
                raise RuntimeError("YANDEX_API_KEY is required for Agent Mode")
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url="https://ai.api.cloud.yandex.net/v1",
                            project=folder, timeout=60.0, max_retries=0)
        self.client = client
        model_name = model or os.getenv("YANDEX_MODEL", "aliceai-llm-flash")
        self.model = model_name if model_name.startswith("gpt://") else f"gpt://{folder}/{model_name}"

    def parse(self, message: str, current_state: dict) -> ParsedTurn:
        """Best-effort strict extraction; malformed output is NEVER applied to state.

        Prefer json_object for DeepSeek, which has intermittently returned empty
        responses with the larger union-heavy json_schema. Retry once with a
        shorter corrective instruction; each response is checked by Pydantic.
        """
        payload = json.dumps({"provided_today": date.today().isoformat(),
                              "current_state": current_state, "message": message},
                             ensure_ascii=False, default=str)
        # This can be overridden for a specific provider/model through .env.
        mode = os.getenv("YANDEX_PARSER_FORMAT", "json_object" if "deepseek" in self.model.lower() else "json_schema")
        if mode not in {"json_schema", "json_object"}:
            raise ValueError("YANDEX_PARSER_FORMAT must be json_schema or json_object")
        schema = ParsedTurn.model_json_schema()
        response_format = ({"type": "json_object"} if mode == "json_object" else
                           {"type": "json_schema", "json_schema": {"name": "parsed_turn", "schema": schema}})
        messages = [{"role": "system", "content": INSTRUCTIONS +
                     "\nОтвет — JSON-объект. Не смешивай operations с legacy-полями. "
                     "Если указан operations, остальные поля оставляй null/пустыми. "
                     "Для удаления используй remove и value=null. "
                     "Не добавляй комментарии, markdown и рассуждения."},
                    {"role": "user", "content": payload}]
        last_error = None
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, response_format=response_format,
                    temperature=0, max_tokens=2400,
                )
            except Exception as exc:
                # Only transient transport/timeouts receive one retry.
                name = type(exc).__name__
                if name in {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"} and attempt == 0:
                    last_error = RuntimeError("Temporary model API failure")
                    continue
                raise
            choice = response.choices[0] if response.choices else None
            content = (choice.message.content or "").strip() if choice else ""
            if not content:
                last_error = ValueError("Model returned empty JSON response")
            elif getattr(choice, "finish_reason", None) == "length":
                last_error = ValueError("Model JSON generation was truncated")
            else:
                try:
                    parsed = ParsedTurn.model_validate_json(content)
                    # Enforce cross-format consistency before mutating the request.
                    if parsed.operations:
                        legacy = ("origin", "destination", "departure_date", "nights", "adults",
                                  "budget_rub", "children_ages", "children_ages_unknown",
                                  "pet_kind", "pet_count", "direct_flights", "meal_plan")
                        if any(getattr(parsed, f) is not None for f in legacy) or parsed.clear_fields or parsed.clear_constraints:
                            raise ValueError("Model mixed operations with legacy fields")
                    return parsed
                except (ValidationError, ValueError) as exc:
                    last_error = ValueError(f"Model JSON failed validation ({type(exc).__name__})")
            if attempt == 0:
                messages.append({"role": "assistant", "content": content or "{}"})
                messages.append({"role": "user", "content":
                                 "Предыдущий ответ был пустым, обрезанным или не соответствовал схеме. "
                                 "Верни заново короткий ВАЛИДНЫЙ JSON-объект; "
                                 "предпочитай legacy-поля, если не уверен в operations. "
                                 "Не выдумывай факты."})
        raise last_error or ValueError("Failed to parse LLM response")
