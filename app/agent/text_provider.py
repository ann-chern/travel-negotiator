"""Specialized prompting through the same model and API key as request parsing."""

import json
import time
from typing import Any

from app.config import ApplicationSettings, load_settings
from app.constants.prompts import TEXT_GENERATION_PROMPTS, TEXT_OUTPUT_STYLE
from app.observability import event as audit_event


class YandexTextGenerator:
    def __init__(
        self,
        *,
        client: Any = None,
        model: str | None = None,
        folder_id: str | None = None,
        settings: ApplicationSettings | None = None,
    ):
        configuration = settings or load_settings()
        yandex = configuration.yandex
        folder = folder_id or yandex.folder_id
        if not folder:
            raise RuntimeError("YANDEX_FOLDER_ID is required")
        if client is None:
            key = yandex.api_key
            if not key:
                raise RuntimeError("YANDEX_API_KEY is required")
            from openai import OpenAI

            client = OpenAI(
                api_key=key,
                base_url=yandex.base_url,
                project=folder,
                timeout=yandex.text_timeout_seconds,
                max_retries=yandex.text_max_retries,
            )
        self.client = client
        self.token_limits = yandex.text_token_limits
        name = model or yandex.model
        self.model = name if name.startswith("gpt://") else f"gpt://{folder}/{name}"

    def generate(self, *, task: str, payload: dict[str, Any]) -> str:
        if task not in TEXT_GENERATION_PROMPTS:
            raise ValueError("Unknown task")
        task_payload = dict(payload)
        skill_instructions = task_payload.pop("skill_instructions", None)
        if task == "recommendation":
            # Pass a compact card representation for the explanation.
            request = task_payload.get("request", {})
            if isinstance(request, dict):
                task_payload["request"] = {
                    k: request.get(k)
                    for k in (
                        "origin",
                        "destination",
                        "departure_date",
                        "date_window",
                        "nights",
                        "travelers",
                        "budget",
                        "preferences",
                    )
                    if request.get(k) is not None
                }
            fields = (
                "id",
                "destination",
                "hotel",
                "price_rub",
                "departure_date",
                "nights",
                "score",
                "hotel_stars",
                "meal_plan",
                "outbound_stops",
                "inbound_stops",
                "tradeoffs",
                "match_reasons",
            )
            task_payload["options"] = [
                {k: row[k] for k in fields if k in row}
                for row in task_payload.get("options", [])
                if isinstance(row, dict)
            ]
        task_payload["output_style"] = TEXT_OUTPUT_STYLE
        messages = [
            {
                "role": "system",
                "content": TEXT_GENERATION_PROMPTS[task]
                + (
                    f"\n\nСпециализированная процедура:\n{skill_instructions}"
                    if skill_instructions
                    else ""
                ),
            },
            {
                "role": "user",
                "content": json.dumps(task_payload, ensure_ascii=False, default=str),
            },
        ]
        audit_event(
            "llm.generate.started",
            task=task,
            provider="yandex",
            model=self.model,
            temperature=0.2,
            payload=task_payload,
        )
        for token_limit in self.token_limits:
            started = time.monotonic()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
                max_tokens=token_limit,
            )
            if not response.choices:
                raise ValueError("No choices in model response")
            choice = response.choices[0]
            if choice.finish_reason == "length":
                continue
            if choice.finish_reason not in ("stop", None):
                raise ValueError(f"Model response not complete: {choice.finish_reason}")
            content = choice.message.content
            if not content or not content.strip():
                raise ValueError("Empty model response")
            result = content.strip()
            usage = getattr(response, "usage", None)
            audit_event(
                "llm.generate.completed",
                task=task,
                output=result,
                finish_reason=choice.finish_reason,
                max_tokens=token_limit,
                elapsed_s=round(time.monotonic() - started, 3),
                tokens_in=getattr(usage, "prompt_tokens", None),
                tokens_out=getattr(usage, "completion_tokens", None),
            )
            return result
        raise ValueError("Model output truncated twice (finish_reason=length)")
