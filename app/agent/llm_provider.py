"""Structured extraction using an optional OpenAI-compatible LLM endpoint."""

from __future__ import annotations

import json
from datetime import date
from typing import Protocol

from pydantic import ValidationError

from app.config import ApplicationSettings, load_settings
from app.constants.prompts import PARSER_INSTRUCTIONS
from app.schemas.agent import ParsedTurn, TurnOperation

INSTRUCTIONS = PARSER_INSTRUCTIONS

__all__ = ["INSTRUCTIONS", "ParsedTurn", "TurnOperation", "TurnParser", "YandexTurnParser"]


class TurnParser(Protocol):
    def parse(self, message: str, current_state: dict) -> ParsedTurn: ...


class YandexTurnParser:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        folder_id: str | None = None,
        client=None,
        settings: ApplicationSettings | None = None,
    ):
        configuration = settings or load_settings()
        yandex = configuration.yandex
        key = api_key or yandex.api_key
        folder = folder_id or yandex.folder_id
        if not folder:
            raise RuntimeError("YANDEX_FOLDER_ID is required for Agent Mode")
        if client is None:
            if not key:
                raise RuntimeError("YANDEX_API_KEY is required for Agent Mode")
            from openai import OpenAI

            client = OpenAI(
                api_key=key,
                base_url=yandex.base_url,
                project=folder,
                timeout=yandex.parser_timeout_seconds,
                max_retries=0,
            )
        self.client = client
        self.settings = configuration
        model_name = model or yandex.parser_model
        self.model = (
            model_name if model_name.startswith("gpt://") else f"gpt://{folder}/{model_name}"
        )

    def parse(self, message: str, current_state: dict) -> ParsedTurn:
        """Best-effort strict extraction; malformed output is NEVER applied to state.

        Prefer json_object for DeepSeek, which has intermittently returned empty
        responses with the larger union-heavy json_schema. Retry once with a
        shorter corrective instruction; each response is checked by Pydantic.
        """
        payload = json.dumps(
            {
                "provided_today": date.today().isoformat(),
                "current_state": current_state,
                "message": message,
            },
            ensure_ascii=False,
            default=str,
        )
        configured_mode = self.settings.yandex.parser_format
        mode = (
            ("json_object" if "deepseek" in self.model.lower() else "json_schema")
            if configured_mode == "auto"
            else configured_mode
        )
        if mode not in {"json_schema", "json_object"}:
            raise ValueError("YANDEX_PARSER_FORMAT must be json_schema or json_object")
        schema = ParsedTurn.model_json_schema()
        response_format = (
            {"type": "json_object"}
            if mode == "json_object"
            else {
                "type": "json_schema",
                "json_schema": {"name": "parsed_turn", "schema": schema},
            }
        )
        messages = [
            {
                "role": "system",
                "content": PARSER_INSTRUCTIONS
                + "\nОтвет — JSON-объект. Не смешивай operations с legacy-полями. "
                "Если указан operations, остальные поля оставляй null/пустыми. "
                "Для удаления используй remove и value=null. "
                "Не добавляй комментарии, markdown и рассуждения.",
            },
            {"role": "user", "content": payload},
        ]
        last_error = None
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format=response_format,
                    temperature=0,
                    max_tokens=self.settings.yandex.parser_max_tokens,
                )
            except Exception as exc:
                # Only transient transport/timeouts receive one retry.
                name = type(exc).__name__
                if (
                    name
                    in {
                        "APITimeoutError",
                        "APIConnectionError",
                        "RateLimitError",
                        "InternalServerError",
                    }
                    and attempt == 0
                ):
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
                            any(getattr(parsed, f) is not None for f in legacy)
                            or parsed.clear_fields
                            or parsed.clear_constraints
                        ):
                            raise ValueError("Model mixed operations with legacy fields")
                    return parsed
                except (ValidationError, ValueError) as exc:
                    last_error = ValueError(f"Model JSON failed validation ({type(exc).__name__})")
            if attempt == 0:
                messages.append({"role": "assistant", "content": content or "{}"})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Предыдущий ответ был пустым, обрезанным или "
                            "не соответствовал схеме. "
                            "Верни заново короткий ВАЛИДНЫЙ JSON-объект; "
                            "предпочитай legacy-поля, если не уверен в operations. "
                            "Не выдумывай факты."
                        ),
                    }
                )
        raise last_error or ValueError("Failed to parse LLM response")
