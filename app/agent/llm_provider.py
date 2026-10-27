"""Structured extraction using an optional OpenAI-compatible LLM endpoint."""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ParsedTurn(BaseModel):
    """LLM output: only changed values; null means 'not mentioned'.

    To remove a field, use clear_fields. A real removal is an explicit operation.
    """
    model_config = ConfigDict(extra="forbid")
    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    nights: int | None = Field(default=None, ge=1, le=60)
    adults: int | None = Field(default=None, ge=1, le=30)
    budget_rub: int | None = Field(default=None, ge=1)
    budget_hard: bool | None = None
    clear_fields: list[Literal["origin", "destination", "departure_date", "nights", "budget"]] = Field(default_factory=list)
    allow_nights: bool = False
    allow_date_shift: bool = False
    allow_budget_increase: bool = False
    intent: Literal["plan", "revise", "other"] = "plan"
    unsupported_requirements: list[str] = Field(default_factory=list)


class TurnParser(Protocol):
    def parse(self, message: str, current_state: dict) -> ParsedTurn: ...


INSTRUCTIONS = """Ты — парсер изменений запроса туриста. Верни только структуру заданной схемы.
Текущий запрос передан в current_state как контекст, но возвращай только НОВЫЕ изменения.
Не угадывай город вылета, даты, число людей или бюджет. Даты трактуй относительно provided_today.
Неприведённое поле = null, явно удалённое пользователем = clear_fields.
Пользователь может разрешить ИСКАТЬ уступки по ночам/датам/бюджету; это не разрешение
изменять текущий запрос без подтверждения. Не делай вид, что неизвестные условия проверены.
При требованиях не представленных полями схемы записывай их в unsupported_requirements.
Бюджет переводи в RUB только если пользователь сообщил RUB/рубли/тысячи рублей;
не конвертируй другую валюту. При неопределённости оставь null.
"""


class YandexTurnParser:
    """Yandex AI Studio OpenAI-compatible Chat Completions JSON-schema parser."""

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
                            project=folder, timeout=25.0, max_retries=1)
        self.client = client
        model_name = model or os.getenv("YANDEX_MODEL", "aliceai-llm-flash")
        self.model = model_name if model_name.startswith("gpt://") else f"gpt://{folder}/{model_name}"

    def parse(self, message: str, current_state: dict) -> ParsedTurn:
        payload = json.dumps({"provided_today": date.today().isoformat(),
                              "current_state": current_state, "message": message},
                             ensure_ascii=False, default=str)
        # A simple, finite flat schema makes provider compatibility easier to test.
        schema = ParsedTurn.model_json_schema()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": INSTRUCTIONS},
                      {"role": "user", "content": payload}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "parsed_turn", "schema": schema}},
            temperature=0, max_tokens=1200,
        )
        if not response.choices or not response.choices[0].message.content:
            raise ValueError("LLM returned no structured content")
        return ParsedTurn.model_validate_json(response.choices[0].message.content)
